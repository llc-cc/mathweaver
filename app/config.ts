export let KnowlegdeGraphSetting = {
    theme: "dark", 
    territory_list: [], 
    // SECURITY: never hardcode DB credentials in client code — they ship in the
    // browser bundle. Sourced from build-time env (VITE_NEO4J_*) instead; empty
    // by default. The previously hardcoded password is in git history and MUST be
    // rotated. See SECURITY_REVIEW.md. (These legacy TianMing routes are not
    // registered in routes.ts, so the main app is unaffected.)
    neo4j_info: {
        uri:      import.meta.env?.VITE_NEO4J_URI ?? "",
        user:     import.meta.env?.VITE_NEO4J_USER ?? "",
        password: import.meta.env?.VITE_NEO4J_PASSWORD ?? "",
    },
    entry_color: {
        dfn : {color : {background: "#D6FEE0", border: "#009C27"}},
        lma : {color : {background: "#DAF0FF", border: "#005B9C"}},
        thm : {color : {background: "#DAF0FF", border: "#005B9C"}},
        ppt : {color : {background: "#FFEDFF", border: "#AC00AF"}},
        crl : {color : {background: "#FFEBD2", border: "#E17C00"}},
        xmp : {color : {background: "#EFDFFF", border: "#7700E5"}},
        cxmp : {color : {background: "#FFD6DC", border: "#D30023"}},
        axm : {color : {background: "#FFFFAC", border: "#C0C000"}},
    }, 
    relation_color: {
        derives : "#FFA500",
        hasProperty : "#00BFFF",
    }, 
}