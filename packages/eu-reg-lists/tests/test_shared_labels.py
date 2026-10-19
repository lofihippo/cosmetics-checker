"""Shared label parsing regressions."""
import pytest
from eu_reg_lists import Registry, Rule, Substance, check_label
from eu_reg_lists.tokenize import tokenize


@pytest.fixture
def registry():
    names = ['Lilial', 'Zinc Pyrithione', '1,4-dioxane',
             'isobutane, if it contains = >0,1% w/w butadiene',
             'butane, if it contains = >0,1 % w/w butadiene',
             'Physostigma venenosum Balf.']
    rule = Rule(status='banned', annex='Annex II', regulation='Fixture regulation',
                source_url='https://example.org/fixture', source_updated_at=None)
    return Registry([Substance(id=str(i), name=n, determination='banned', rules=[rule])
                     for i, n in enumerate(names)] +
                    [Substance(id='water', name='Aqua', aliases=['Water']),
                     Substance(id='alcohol', name='Alcohol Denat.')],
                    data_updated_at='2026-10-17T00:00:00Z')


@pytest.mark.parametrize('name', [
    'isobutane, if it contains = >0,1% w/w butadiene',
    'butane, if it contains = >0,1 % w/w butadiene',
    'Physostigma venenosum Balf.',
])
def test_known_name_punctuation_survives_cleanup(registry, name):
    report = check_label(f'Aqua, {name}, Unknown', registry)
    assert name.lower() in report['tokens']
    assert report['n_banned'] == 1 and report['verdict'] == 'FLAGGED'
    result = next(r for r in report['results'] if r.ingredient == name.lower())
    assert result.regulation and result.source_url
    assert result.data_updated_at == report['data_updated_at'] == registry.data_updated_at


@pytest.mark.parametrize('text', ['Alcohol Denat.', 'Alcohol Denat. 10%', 'Alcohol Denat. (10%)'])
def test_period_in_known_inci_name(registry, text):
    report = check_label(text, registry)
    assert report['tokens'] == ['alcohol denat.']
    assert report['n_not_in_dataset'] == 0
    assert report['n_unverified_identity'] == 1


@pytest.mark.parametrize('text', ['Parfum (Lilial)', 'Aqua; Zinc Pyrithione', '1,4-dioxane',
                                  'Aqua [contient Lilial]', 'Zinc Pyrithione (1%)'])
def test_public_checker_and_linter_agree(registry, text):
    from eu_ingredient_linter import ingredient_report
    public = check_label(text, registry)
    linter = ingredient_report(text, registry)
    for key in public:
        assert ([r.as_dict() for r in public[key]] if key == 'results' else public[key]) == linter[key]
    assert public['n_banned'] == 1


def test_unknown_punctuation_remains_unresolved(registry):
    report = check_label('Something Else. 10%', registry)
    assert report['n_not_in_dataset'] == 1 and not report['clean']
    assert tokenize('Aqua. May contain [+/-]: CI 99999', registry.phrases) == ['aqua', 'ci 99999']


def test_explicit_tokenizer_remains_supported(registry):
    assert check_label('custom', registry, lambda text, phrases: ['Lilial'])['n_banned'] == 1
