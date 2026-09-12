from pathlib import Path
from beacon_skill.transports import __all__ as transports_all, ConwayClient


def test_transports_count_and_conway_presence():
    # Verify export in package
    assert 'ConwayClient' in transports_all
    assert ConwayClient is not None

    # Verify pyproject.toml
    pyproject = Path('pyproject.toml').read_text(encoding='utf-8')
    assert '13 transports' in pyproject
    assert 'Conway' in pyproject

    # Verify README.md
    readme = Path('README.md').read_text(encoding='utf-8')
    assert '**13 transports**' in readme
    assert '**12 transports**' not in readme
    assert 'across 13 transport layers' in readme
    assert 'across 12 transport layers' not in readme
    assert '13 transport layers: BoTTube' in readme
    assert '12 transport layers: BoTTube' not in readme
    assert '### Conway' in readme

    # Verify SKILL.md
    skill = Path('SKILL.md').read_text(encoding='utf-8')
    assert '**13 transports**' in skill
    assert '**12 transports**' not in skill
    assert 'across **13 platforms**' in skill
    assert 'across **12 platforms**' not in skill

    # Verify llms.txt
    llms = Path('llms.txt').read_text(encoding='utf-8')
    assert 'across 13 transport layers' in llms
    assert 'across 12 transport layers' not in llms
    assert '- **13 Transports**:' in llms
    assert '- **12 Transports**:' not in llms
    assert 'Beacon supports 13 transport layers' in llms
    assert 'Beacon supports 12 transport layers' not in llms
