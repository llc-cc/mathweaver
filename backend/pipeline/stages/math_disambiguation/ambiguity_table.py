"""Mathematical ambiguity table.

The table uses a simple shape:

{
    "symbol": ["meaning 1", "meaning 2", ...],
}

Matching only uses the dict keys. The value lists are preserved for
human-readable context and downstream prompt construction.
"""

DEFAULT_AMBIGUITY_TABLE = {
    "'": [
        "derivative",
        "partial derivative",
        "new symbol",
        "derived subgroup",
        "sequence index"
    ],

    "*": [
        "multiplication",
        "convolution",
        "group operation",
        "adjoint operator"
    ],

    "\\cdot": [
        "multiplication",
        "dot product",
        "scalar multiplication"
    ],

    "|...|": [
        "absolute value",
        "cardinality",
        "order"
    ],

    "||...||": [
        "norm",
        "absolute value"
    ],

    "|": [
        "divides",
        "conditional probability"
    ],
    
    "/": [
        "division",
        "quotient group",
        "set difference"
    ],

    "\\circ": [
        "function composition",
        "group composition"
    ],

    "~": [
        "equivalence",
        "asymptotic equivalence",
        "distribution"
    ],

    "\\partial": [
        "partial derivative",
        "boundary operator"
    ],

    "\\Delta": [
        "difference operator",
        "Laplacian operator"
    ],

    "\\nabla": [
        "gradient operator",
        "vector differential operator"
    ],

    "\\sum": [
        "summation",
        "direct sum"
    ],

    "\\prod": [
        "product",
        "Cartesian product"
    ],

    "\\to": [
        "mapping",
        "limit",
        "implication"
    ],

    "\\subset": [
        "subset",
        "subspace"
    ],

    "^T": [
        "transpose",
        "superscript index"
    ],

    "^*": [
        "adjoint",
        "conjugate",
        "dual"
    ]
}


def get_ambiguity_table(custom_table=None):
    """Return the caller-provided table or the local default table."""
    if custom_table is not None:
        return custom_table
    return DEFAULT_AMBIGUITY_TABLE
