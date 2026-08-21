import React, { useContext } from "react";

import { Button } from "~/components/Interact/Button";
import { Select } from "~/components/Interact/Select";
import { Settings } from "../Settings";

// Language options for the header select
const Language_Options = [
    { label: "🇺🇸English", value: "EN" },
    { label: "🇨🇳简体中文", value: "CN" },
]

export function Header( {headline}: {headline?: string} ) {
    const settings = useContext(Settings);
    const setLanguage = settings.setLanguage || (() => {});

    return (
        <header>
            
            <h1>{headline || "猫猫点亮了数学的天空"}</h1>
            <div className="select-container" style={{position: "absolute", right: "10px", top: "10px"}}>
                <Select 
                    option_list={Language_Options}
                    onChange={e => { setLanguage(e.target.value); }}
                />
            </div>
        </header>
    );
}