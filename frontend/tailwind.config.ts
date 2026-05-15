import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#0b0d10",
        panel: "#12161b",
        border: "#1f262d",
        accent: "#5eead4",
        muted: "#6b7280",
      },
    },
  },
  plugins: [],
};
export default config;
