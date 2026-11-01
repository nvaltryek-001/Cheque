"""Consistency of the deployment files (Dockerfile, render.yaml, render.paid.yaml, .dockerignore) with each other and with the application.

Docker and Render cannot be run here, so these tests read the files the way the rehearsal script does
(scripts/deploy_simulation.py) and fail when one file drifts away from another - for example a data folder that is
no longer on the persistent disk, which would lose data on every redeploy."""
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deploy_simulation", ROOT / "scripts" / "deploy_simulation.py")
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)

DOCKER_ENV = sim.dockerfile_env(ROOT / "Dockerfile")
# render.yaml is the Free-plan blueprint (what the one-click link uses; no disk, data is temporary).
# render.paid.yaml is the Starter-plan blueprint with the 1 GB persistent disk.
FREE_BLUEPRINT = {e["key"]: e for e in sim.blueprint_env(ROOT / "render.yaml")}
BLUEPRINT = {e["key"]: e for e in sim.blueprint_env(ROOT / "render.paid.yaml")}
MOUNT = sim.disk_mount(ROOT / "render.paid.yaml")
DOCKERFILE = (ROOT / "Dockerfile").read_text()
RENDER_FREE = (ROOT / "render.yaml").read_text()
RENDER = (ROOT / "render.paid.yaml").read_text()                   # the persistent-disk tests read the paid blueprint
BLUEPRINTS = {"render.yaml": (FREE_BLUEPRINT, RENDER_FREE), "render.paid.yaml": (BLUEPRINT, RENDER)}


def test_every_folder_that_must_survive_a_redeploy_is_on_the_persistent_disk():
    for key in ("CV_INSTANCE_DIR", "CV_MODELS_DIR", "CV_REPORTS_DIR"):
        assert DOCKER_ENV[key].startswith(MOUNT + "/"), f"{key}={DOCKER_ENV[key]} is not under the disk mounted at {MOUNT}"
    assert BLUEPRINT["CV_INSTANCE_DIR"]["value"] == DOCKER_ENV["CV_INSTANCE_DIR"]
    for key in ("CV_DATABASE", "CV_STORAGE_DIR"):                           # if someone sets these they must be on the disk too
        for source in (DOCKER_ENV, {k: v.get("value", "") for k, v in BLUEPRINT.items()}):
            if source.get(key):
                assert source[key].startswith(MOUNT + "/")
    assert re.search(r"^\s+plan:\s*(?!free\b)\w+", RENDER, flags=re.M), "a persistent disk needs a paid plan"
    assert int(re.search(r"sizeGB:\s*(\d+)", RENDER).group(1)) >= 1


def test_the_entrypoint_is_the_command_and_the_image_does_not_force_a_user():
    """The entrypoint starts as root to hand the (root-owned) disk to the app user and then drops privileges itself."""
    assert re.search(r'^CMD \["python", "docker_entrypoint.py"\]', DOCKERFILE, flags=re.M)
    assert not re.search(r"^USER\s", DOCKERFILE, flags=re.M)
    assert "useradd --create-home --uid 10001 app" in DOCKERFILE
    assert re.search(r"^HEALTHCHECK .*PORT", DOCKERFILE, flags=re.M)


@pytest.mark.parametrize("name", sorted(BLUEPRINTS))
def test_the_blueprint_asks_for_secrets_instead_of_containing_them(name):
    blueprint, _ = BLUEPRINTS[name]
    assert blueprint["CV_ADMIN_PASSWORD"].get("prompt") and "value" not in blueprint["CV_ADMIN_PASSWORD"]
    assert blueprint["CV_SECRET_KEY"].get("generate") and "value" not in blueprint["CV_SECRET_KEY"]
    for key, item in blueprint.items():
        if re.search(r"(PASSWORD|SECRET|TOKEN|KEY)$", key):
            assert "value" not in item, f"{key} has a literal value in {name}"


@pytest.mark.parametrize("name", sorted(BLUEPRINTS))
def test_the_blueprint_settings_the_application_depends_on(name):
    blueprint, text = BLUEPRINTS[name]
    assert blueprint["PORT"]["value"] == "10000" and DOCKER_ENV["PORT"] == "8000"          # Render's own value wins at run time
    assert blueprint["CV_PROXY_HOPS"]["value"] == "1"                                     # one TLS-terminating proxy in front
    assert int(blueprint["CV_MIN_PASSWORD_LENGTH"]["value"]) >= 12
    assert "/api/health" in text and re.search(r"healthCheckPath:\s*/api/health", text)
    assert blueprint["CV_INSTANCE_DIR"]["value"] == DOCKER_ENV["CV_INSTANCE_DIR"]


def test_memory_settings_for_a_512_mb_instance_are_in_the_image():
    for key in ("MALLOC_ARENA_MAX", "MALLOC_MMAP_THRESHOLD_", "MALLOC_TRIM_THRESHOLD_"):
        assert key in DOCKER_ENV, f"{key} missing from the Dockerfile"
    assert DOCKER_ENV["MALLOC_ARENA_MAX"] == "2"
    for blueprint, _ in BLUEPRINTS.values():                       # Free and Starter both have 512 MB
        assert int(blueprint["CV_MAX_IMAGE_PIXELS"]["value"]) <= 12_500_000
        assert int(blueprint["CV_MAX_CONCURRENT_IMAGES"]["value"]) <= 2
    # the image is safe in 512 MB on its own, not only through the blueprint: two 25 MP scans peaked at 540 MB in the rehearsal
    assert int(DOCKER_ENV["CV_MAX_IMAGE_PIXELS"]) <= 12_500_000
    assert int(DOCKER_ENV["CV_MAX_CONCURRENT_IMAGES"]) <= 2


def test_private_data_cannot_enter_the_image():
    ignored = {line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()}
    for must in (".git", "dataset", "signature", "legacy", "backend/instance", "backend/.env"):
        assert must in ignored, f".dockerignore does not exclude {must}"
    assert "COPY dataset" not in DOCKERFILE and "COPY signature" not in DOCKERFILE


@pytest.mark.parametrize("name", sorted(BLUEPRINTS))
def test_the_blueprint_parser_agrees_with_a_real_yaml_parser(name):
    yaml = pytest.importorskip("yaml")
    blueprint, text = BLUEPRINTS[name]
    parsed = yaml.safe_load(text)["services"][0]
    real = {e["key"]: e for e in parsed["envVars"]}
    assert set(real) == set(blueprint)
    for key, item in real.items():
        if "value" in item:
            assert blueprint[key]["value"] == str(item["value"])
        assert bool(blueprint[key].get("generate")) == bool(item.get("generateValue"))
        assert bool(blueprint[key].get("prompt")) == (item.get("sync") is False)
    if name == "render.paid.yaml":
        assert parsed["disk"]["mountPath"] == MOUNT


def test_the_free_blueprint_has_no_disk_and_says_so():
    """A Free instance cannot have a disk, so everything it stores is temporary: the file must not pretend otherwise."""
    yaml = pytest.importorskip("yaml")
    service = yaml.safe_load(RENDER_FREE)["services"][0]
    assert service["plan"] == "free" and service["runtime"] == "docker"
    assert "disk" not in service, "a disk cannot be attached to a Free instance"
    assert service["autoDeployTrigger"] == "off"                    # a push must not restart a service whose data is temporary
    explanation = RENDER_FREE.split("services:")[0]
    for needle in ("No persistent disk", "erased", "0.1 CPU", "render.paid.yaml"):
        assert needle in explanation, f"the Free blueprint no longer explains: {needle}"
    assert int(FREE_BLUEPRINT["CV_MAX_CONCURRENT_IMAGES"]["value"]) == 1       # a tenth of a CPU: one image at a time
    assert int(FREE_BLUEPRINT["CV_IMAGE_QUEUE_SECONDS"]["value"]) >= 60        # a queued upload waits instead of failing at once


def test_the_paid_blueprint_keeps_the_disk_and_a_paid_plan():
    yaml = pytest.importorskip("yaml")
    service = yaml.safe_load(RENDER)["services"][0]
    assert service["plan"] != "free" and service["disk"]["mountPath"] == MOUNT and service["disk"]["sizeGB"] >= 1


def test_the_two_blueprints_differ_only_where_the_plan_requires_it():
    plan_specific = {"CV_MAX_CONCURRENT_IMAGES", "CV_IMAGE_QUEUE_SECONDS"}
    assert set(BLUEPRINT) <= set(FREE_BLUEPRINT) and set(FREE_BLUEPRINT) - set(BLUEPRINT) <= plan_specific
    for key in set(BLUEPRINT) & set(FREE_BLUEPRINT):
        if key not in plan_specific:
            assert BLUEPRINT[key] == FREE_BLUEPRINT[key], f"{key} differs between render.yaml and render.paid.yaml"


def test_the_ci_workflow_is_valid_yaml_and_still_runs_the_container_checks():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    assert {"docker"} <= set(wf["jobs"])
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for needle in ("smoke_live.py", "docker restart", "persistence_check.py"):
        assert needle in text, f"the docker job no longer runs {needle}"
