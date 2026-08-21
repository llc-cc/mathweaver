interface Option {
    label: string;
    value: string;
}

export function Select( {option_list, onChange} : {option_list: Option[], onChange: (e: React.ChangeEvent<HTMLSelectElement>) => void}) {
    return (
        <select onChange={onChange}>
            {option_list.map((option, index) => (
                <option key={index} value={option.value}>
                    {option.label}
                </option>
            ))}
        </select>
    )
}