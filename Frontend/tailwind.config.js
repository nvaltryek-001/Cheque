/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#07152e",
        panel: "#0c1d3a",
        electric: "#1478ff",
        cyan: "#00d7f5"
      },
      boxShadow: {
        glow: "0 0 32px rgba(20,120,255,.18)"
      }
    }
  },
  plugins: []
};