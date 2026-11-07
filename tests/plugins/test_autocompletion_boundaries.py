"""Model-independent regressions for insertions before existing Markdown closing marks."""

import pytest

from backend.config import APP_ROOT
from backend.plugins.loading import load_module


@pytest.fixture(scope="module")
def prompt():
    module, _ = load_module("autocompletion", APP_ROOT / "plugins/autocompletion/server")
    return module.prompt


@pytest.mark.parametrize("fim", [True, False])
@pytest.mark.parametrize(
    "prefix,suffix,raw,want",
    [
        ("Energy $E = ", "$.", "mc^2$. More prose", "mc^2"),
        ("Energy $E = ", "$", "mc^2", "mc^2"),
        ("Energy $$E = ", "$$.", "mc^2$$. More prose", "mc^2"),
        (r"Cost $\text{", "}$.", r"cost \$5}$. More prose", r"cost \$5"),
        ("Energy $", "$.", r"\text{cost \$5} + x$. More", r"\text{cost \$5} + x"),
        ("Energy $", "$", r"x\\$ beyond", r"x\\"),
        ("Value (", ").", "f(x)) more", "f(x)"),
        ("Value (", ").", "f(x)", "f(x)"),
        ("Value (", ").", "f([x, {y}])) more", "f([x, {y}])"),
        ("Value (", ").", r"\(literal\)) more", r"\(literal\)"),
        ("Value (", ").", 'the string ")" and f(x)) beyond', 'the string ")" and f(x)'),
        ("Value (", ").", "the symbol `)` and f(x)) more", "the symbol `)` and f(x)"),
        ("Value (", ").", "the symbol ``)`x`` and f(x)) more", "the symbol ``)`x`` and f(x)"),
        ("Value (", ").", "$x)$ and f(x)) more", "$x)$ and f(x)"),
        ("Value [", "]", "f([x])] beyond", "f([x])"),
        (r"$\frac{a", "}$", "}{b}}$ more", ""),
        (r"$\frac{", "}$", r"\sqrt{x}}$ more", r"\sqrt{x}"),
        (r"$\text{", "}$", r"set \{x\}}$ more", r"set \{x\}"),
        ("**The state is ", "**.", "mixed**. More", "mixed"),
        ("**The state is ", "**", "*locally* mixed**. More", "*locally* mixed"),
        ("*The state is ", "*.", "**locally** mixed*. More", "**locally** mixed"),
        ("*The state is ", "*", "**mixed*** beyond", "**mixed**"),
        ("**The state is ", "**", "*mixed*** beyond", "*mixed*"),
        ("***The state is ", "***", "mixed*** beyond", "mixed"),
        ("**Energy ", "**.", "$a*b$ remains**. More", "$a*b$ remains"),
        ("_Use ", "_", "snake_case_name_ more", "snake_case_name"),
        ("__Use ", "__", "snake_case_name__ more", "snake_case_name"),
        ("++The state is ", "++.", "mixed++. More", "mixed"),
        ("~~The state is ", "~~.", "mixed~~. More", "mixed"),
        ('The state is "', '".', r'a \"mixed\" state". More', r"a \"mixed\" state"),
        ("A '", "'.", "state's purity'. More", "state's purity"),
        ("A 'state", "'", "'s purity' more", "'s purity"),
        ("Use ``", "``.", "a `tick` symbol`` more", "a `tick` symbol"),
        ("The value is ", ".", "1.25.", "1.25"),
        ("The value is ", ".", "1.25", "1.25"),
        ("The state is ", "...", "mixed...", "mixed"),
        ("The value is ", ".", r"literal\.", r"literal\."),
        ("The value is ", ".", r"literal\\.", r"literal\\"),
        ("The state is ", "", "mixed.", "mixed."),
        ("The state is ", "\n\nNext", "mixed.", "mixed."),
        ("**The state is ", " **.", "mixed**. More", "mixed"),
    ],
)
def test_completion_stays_inside_existing_closer(prompt, fim, prefix, suffix, raw, want):
    assert prompt.clean(raw, prefix, suffix, "line", fim=fim) == want


def test_plain_completion_keeps_its_existing_text_overlap_handling(prompt):
    assert prompt.clean(" the state", "We obtain", "state", "line", fim=False) == " the"
    assert prompt.clean(" the state", "We obtain", "state", "line", fim=True) == " the state"


def test_long_whitespace_suffix_is_trimmed_quickly(prompt):
    import time

    start = time.monotonic()
    prompt.trim("text", "", "\t" * 200_000 + "x", fim=False)
    prompt.trim("text", "", " \t" * 100_000 + ".", fim=True)
    assert time.monotonic() - start < 2
