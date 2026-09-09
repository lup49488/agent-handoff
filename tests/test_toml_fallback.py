"""The built-in TOML reader, checked against the real one.

Python 3.9 and 3.10 have no `tomllib`, so on those versions this reader *is*
how every config is loaded. It went untested there for a long time and was
wrong in two ways that only Windows users and anyone with a comma in a command
line would ever hit.

The strongest test available is differential: on 3.11+ both implementations
are present, so the fallback can be required to agree with the real one.
"""

import pytest

from agent_handoff import config as cfg

tomllib = pytest.importorskip("tomllib", reason="3.11+ has the reference reader")


#: Configurations shaped like the ones this tool actually writes and reads.
CASES = {
    "the documented shape": """
        primary = "codex"
        fallback = ["claude-code"]
    """,
    "a multi-line array": """
        fallback = [
            "claude-code",
            "codex",
        ]
    """,
    "comments and types": """
        # a comment
        primary = "codex"  # trailing
        checkpoint_protocol = false
        stall_timeout_seconds = 900
    """,
    "a hash inside a string": '''
        primary = "co#dex"
    ''',
    "tables": """
        [agents.opencode]
        exec = ["run", "{prompt}"]
    """,
    "a windows path": r'''
        [agents.mine]
        executable = "D:\\software\\Python310\\python.exe"
    ''',
    "a comma inside a command line": '''
        [agents.mine]
        exec = ["-c", "print('a', 'b')", "{prompt}"]
    ''',
    "a bracket inside a string": '''
        [agents.mine]
        exec = ["-c", "print(sys.argv[-1])", "{prompt}"]
    ''',
    "escapes": r'''
        [agents.mine]
        note = "a\ttab, a \"quote\", a\\backslash"
    ''',
    "a literal string keeps its backslashes": r"""
        [agents.mine]
        executable = 'C:\bin\agent.exe'
    """,
    "unicode escapes": r'''
        [agents.mine]
        note = "\u00b7 dot"
    ''',
    "a float": """
        stall_timeout_seconds = 12.5
    """,
    "an empty array": """
        fallback = []
    """,
}


@pytest.mark.parametrize("text", list(CASES.values()), ids=list(CASES))
def test_the_fallback_agrees_with_tomllib(text):
    import textwrap

    text = textwrap.dedent(text)
    assert cfg.parse_toml(text) == tomllib.loads(text)


def test_a_realistic_config_round_trips():
    starter = cfg.STARTER.replace("__PRIMARY__", "codex").replace("__FALLBACK__", "claude-code")
    assert cfg.parse_toml(starter) == tomllib.loads(starter)


def test_a_config_written_for_a_real_agent_round_trips():
    # The exact shape that broke on 3.10: a Windows executable and a script
    # argument containing both a comma and brackets.
    text = (
        'primary = "mine"\n'
        'fallback = ["codex"]\n'
        "\n"
        "[agents.mine]\n"
        'executable = "D:\\\\software\\\\Python310\\\\python.exe"\n'
        'exec = ["-c", "import sys; print(\'x\', sys.argv[-1][:20])", "{prompt}"]\n'
    )
    ours = cfg.parse_toml(text)
    assert ours == tomllib.loads(text)
    assert ours["agents"]["mine"]["executable"] == "D:\\software\\Python310\\python.exe"
    assert ours["agents"]["mine"]["exec"][1] == "import sys; print('x', sys.argv[-1][:20])"


def test_what_it_refuses_it_refuses_loudly():
    for bad in ("primary = codex", 'fallback = [\n  "claude-code",\n', 'note = "\\q"'):
        with pytest.raises(cfg.ConfigError):
            cfg.parse_toml(bad)
