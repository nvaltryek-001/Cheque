# ChequeVerify AI Organizer

Run the PowerShell script from Windows PowerShell:
```powershell
Set-ExecutionPolicy -Scope Process Bypass
& "$env:USERPROFILE\Downloads\Organize-ChequeVerifyAI.ps1"
```
Or execute the script directly from its downloaded location.

The script:
- Flattens `new_datset\new_datset` only if it is a pure one-level nested dataset with no collisions.
- Moves known root-level helper scripts into categorized `scripts` subfolders.
- Preserves `backend`, `Frontend`, `training`, `services`, `reports`, model assets, and virtual environments.
- Removes Python generated caches only.
- Writes `PROJECT_STRUCTURE.md`.

It does not blindly delete files or rename the dataset root, because backend/training paths may depend on `new_datset`.
