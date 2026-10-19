"""Label parsing regressions."""
import json
from pathlib import Path

import pytest
from eu_ingredient_linter import ingredient_report
from eu_ingredient_linter.tokenize import tokenize
from eu_reg_lists import Registry, Rule, Substance

SLASH_NAMES = ["Acrylates/C10-30 Alkyl Acrylate Crosspolymer",
               "Ammonium Acryloyldimethyltaurate/VP Copolymer"]


@pytest.fixture
def registry():
    # Retained curated fixture, not a claim of current authoritative coverage.
    raw = json.loads((Path(__file__).resolve().parents[3] / 'data/eu_flags.json').read_text())
    subs = [Substance(**{**s, 'rules': [Rule(**r) for r in s.get('rules', [])]}) for s in raw['substances']]
    subs += [Substance(id=f'polymer-{i}', name=n) for i, n in enumerate(SLASH_NAMES)]
    subs.append(Substance(id='iron-test', name='Iron test', ci='77491'))
    return Registry(subs, data_updated_at=raw['meta']['data_updated_at'])


@pytest.mark.parametrize(('label', 'expected'), [
    ('Aqua, Parfum (Lilial), Glycerin', ['aqua', 'parfum', 'lilial', 'glycerin']),
    ('Aqua (Water), Fragrance (contains Zinc Pyrithione)',
     ['aqua (water)', 'water', 'fragrance', 'zinc pyrithione']),
    ('Camellia Sinensis (Leaf, Root) Extract, Glycerin',
     ['camellia sinensis extract', 'glycerin']),
    (SLASH_NAMES[0], [SLASH_NAMES[0].lower()]),
    (SLASH_NAMES[1], [SLASH_NAMES[1].lower()]),
    ('Aqua, Glycerin. May contain [+/-]: CI 77491, CI 77891',
     ['aqua', 'glycerin', 'ci 77491', 'ci 77891']),
    ('Water (Aqua (Eau)), Glycerin', ['water', 'aqua', 'eau', 'glycerin']),
    ('Aqua (), Glycerin', ['aqua', 'glycerin']),
    ('(Lilial)', ['lilial']),
    ('(contains Lilial)', ['lilial']),
    ('(Unknown)', ['unknown']),
    ('()', []),
    ('Aqua [contains Lilial; Zinc Pyrithione], Glycerin',
     ['aqua', 'lilial', 'zinc pyrithione', 'glycerin']),
    ('Aqua (Water, Lilial, Glycerin', ['aqua (water', 'lilial', 'glycerin']),
    ('Aqua [Water), Lilial; Glycerin', ['aqua [water)', 'lilial', 'glycerin']),
    ('Aqua), Lilial, Glycerin', ['aqua)', 'lilial', 'glycerin']),
    ('Aqua (Unknown (Lilial)), Glycerin', ['aqua', 'lilial', 'glycerin']),
    ('Zinc Pyrithione (1%), Glycerin 0,5%', ['zinc pyrithione', 'glycerin']),
    ('Water/EAU/Aqua', ['water', 'eau', 'aqua']),
])
def test_label_tokens(registry, label, expected):
    assert tokenize(label, registry.phrases) == expected


def test_parenthetical_ban_and_provenance(registry):
    report = ingredient_report('Aqua, Parfum (Lilial), Glycerin', registry)
    assert report['n_banned'] >= 1
    assert report['verdict'] == 'FLAGGED'
    result = next(r for r in report['results'] if r['ingredient'] == 'lilial')
    assert result['derived_from'] == 'parfum (lilial)'
    assert result['regulation'] and result['source_url']
    assert result['data_updated_at'] == registry.data_updated_at


def test_translation_does_not_add_unknowns(registry):
    baseline = ingredient_report('Aqua, Glycerin', registry)
    for label in ['Aqua (Water), Glycerin', 'Aqua (Fragrance), Glycerin',
                  'Aqua (Unknown (Leaf)), Glycerin']:
        assert ingredient_report(label, registry)['n_not_in_dataset'] == baseline['n_not_in_dataset']


@pytest.mark.parametrize('marker', ['may contain', 'MaY CoNtAiN [+/-]:', '+/-',
                                   '[+/-]', '(+/-)', 'PEUT CONTENIR:'])
def test_optional_colorants_are_checked(registry, marker):
    report = ingredient_report(f'Aqua, Glycerin. {marker} CI 77491, CI 77891', registry)
    result = next(r for r in report['results'] if r['ingredient'] == 'ci 77491')
    assert result['is_in_dataset'] is True


@pytest.mark.parametrize('prefix', ['contains', 'contient', 'incl.', 'INCL. :'])
def test_inner_qualifiers(registry, prefix):
    assert ingredient_report(f'Parfum ({prefix} Zinc Pyrithione)', registry)['n_banned'] == 1


def test_ambiguous_candidates_stay_unresolved():
    reg = Registry([Substance(id='a', name='A', aliases=['shared']),
                    Substance(id='b', name='B', aliases=['shared'])])
    assert ingredient_report('Parfum (shared)', reg)['tokens'] == ['parfum']
    report = ingredient_report('shared', reg)
    assert report['n_not_in_dataset'] == 1 and not report['clean']


def test_known_bracket_name_and_nano_forms():
    name = 'Camellia Sinensis (Leaf, Root) Extract'
    assert tokenize(name, [name]) == [name.lower()]
    assert tokenize('Zinc oxide (nano)', ['zinc oxide']) == ['zinc oxide (nano)']
    reg = Registry([Substance(id='z', name='Zinc oxide')])
    assert ingredient_report('Zinc oxide (nano)', reg)['n_not_in_dataset'] == 1


def test_inner_identifiers_use_registry_lookup():
    reg = Registry([Substance(id='x', name='X', cas='71-43-2')])
    assert ingredient_report('Parfum (71-43-2)', reg)['tokens'] == ['parfum', '71-43-2']


def test_deep_nesting_and_unbalanced_suffix(registry):
    report = ingredient_report('(' * 2000 + 'Lilial' + ')' * 2000 + ', Glycerin', registry)
    assert report['n_banned'] == 1
    assert report['tokens'][-1] == 'glycerin'
    assert 'lilial' in tokenize('(' * 4000 + ', Lilial', registry.phrases)


def test_protected_longer_span_and_word_boundaries():
    name = 'Acrylates/C10-30 Alkyl Acrylate Crosspolymer'
    assert tokenize(name, ['acrylates/c10-30', name]) == [name.lower()]
    assert tokenize('not1,4-dioxane', ['1,4-dioxane']) == ['not1', '4-dioxane']
    assert tokenize('1,4-dioxanes', ['1,4-dioxane']) == ['1', '4-dioxanes']


def test_known_outer_name_does_not_hide_inner_ban():
    reg = Registry([Substance(id='p', name='Parfum (Lilial)'),
                    Substance(id='l', name='Lilial', determination='banned',
                              rules=[Rule(status='banned', annex='Annex II', regulation='fixture',
                                          source_url='https://example.org/fixture', source_updated_at=None)])])
    assert ingredient_report('Parfum (Lilial)', reg)['n_banned'] == 1


def test_qualifier_prefix_must_not_guess_a_substance(registry):
    # Only the documented abbreviation "incl." is a qualifier. A larger,
    # unresolved word beginning "incl" must not become a different identity.
    assert ingredient_report('Parfum (incllilial)', registry)['tokens'] == ['parfum']


def test_supplementary_aliases_count_once_without_losing_candidates(registry):
    report = ingredient_report('Lilial (Butylphenyl methylpropional)', registry)
    assert report['tokens'] == ['lilial', 'butylphenyl methylpropional']
    assert report['n_candidates'] == 2
    assert report['n_ingredients'] == report['n_banned'] == report['n_flagged'] == 1
    assert report['results'][0]['matched_aliases'] == ['butylphenyl methylpropional']
    assert report['results'][0]['regulation'] and report['results'][0]['source_url']


def test_distinct_occurrences_and_substances_are_not_merged(registry):
    text = 'Parfum (Lilial, Butylphenyl methylpropional, Zinc Pyrithione), Lilial (Butylphenyl methylpropional)'
    report = ingredient_report(text, registry)
    assert report['n_candidates'] == 6
    assert report['n_ingredients'] == 4 and report['n_banned'] == 3
    assert report['results'][1]['derived_from'].startswith('parfum (')
    assert report['results'][1]['matched_aliases'] == ['butylphenyl methylpropional']


def test_nested_translation_aliases_count_once(registry):
    report = ingredient_report('Water (Aqua (Eau)), Glycerin', registry)
    assert report['n_candidates'] == 4 and report['n_ingredients'] == 2
    assert report['results'][0]['matched_aliases'] == ['aqua', 'eau']
    assert report['n_not_in_dataset'] == 0


def test_forms_sharing_identifiers_remain_distinct():
    reg = Registry([Substance(id='z', name='Zinc oxide', cas='1314-13-2'),
                    Substance(id='zn', name='Zinc oxide (nano)', cas='1314-13-2')])
    report = ingredient_report('Zinc oxide (contains Zinc oxide (nano))', reg)
    assert report['n_ingredients'] == 2
    assert all(not r['matched_aliases'] for r in report['results'])
