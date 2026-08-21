import React, { createContext } from "react";

interface SettingsInterface {
    theme: string;
    language: string;
    setLanguage?: (lang: string) => void;
    setTheme?: (theme: string) => void;
}

// Provide default values including no-op setters so consumers can safely call them
export let Settings = createContext<SettingsInterface>({
    theme: "dark",
    language: "EN",
    setLanguage: () => {},
    setTheme: () => {},
});