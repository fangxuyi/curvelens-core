import pyarrow as pa
import pytest

from ccvm.analytics.option_comparison import compare_front_surface, front_surface
from ccvm.workflow.finalize import AnalysisValidationError, _check_option_comparisons
from ccvm.reporting.daily_report import _market_risk_section


def surface(expiry='2026-11-24', underlying='GCZ26', iv=.20168666):
    return dict(option_expiry=expiry, underlying_contract=underlying, atm_iv=iv,
                risk_reversal_25d=.01, butterfly_25d=.02, skew_slope=.03)


def compare(today, prior):
    return compare_front_surface(pa.Table.from_pylist(today), pa.Table.from_pylist(prior),
                                 '2026-10-27', '2026-10-26')


def test_gold_roll_uses_same_expiry_not_previous_headline():
    result = compare([surface()], [surface('2026-10-27', iv=.15), surface(iv=.20524347)])
    assert result['headline_surface_changed']
    assert result['atm_iv_change_pp'] == pytest.approx(-.355681)
    assert result['direction'] == 'declined'
    assert '20.524347%' in result['comparison_text']
    assert '-0.355681 percentage points' in result['comparison_text']


@pytest.mark.parametrize('prior', [surface('2026-10-27'), surface(underlying='GCG27'), surface(iv=None), surface(iv=float('nan'))])
def test_missing_or_invalid_same_contract_does_not_substitute(prior):
    result = compare([surface()], [prior])
    assert result['status'] == 'unavailable'
    assert result['atm_iv_change'] is None


@pytest.mark.parametrize('prior,expected', [(.19, 'increased'), (.22, 'declined'), (.20168666, 'unchanged')])
def test_same_contract_direction(prior, expected):
    result = compare([surface()], [surface(iv=prior)])
    assert not result['headline_surface_changed']
    assert result['direction'] == expected


def test_unsorted_rows_and_null_front_do_not_mix_surfaces():
    table = pa.Table.from_pylist([surface('2026-12-22'), surface(iv=None)])
    result = front_surface(table)
    assert result['option_expiry'] == '2026-11-24'
    assert result['atm_iv'] is None
    assert _market_risk_section(None, table)['options']['atm_iv'] is None


def test_conflicting_duplicate_surface_is_unavailable():
    result = compare([surface()], [surface(iv=.2), surface(iv=.3)])
    assert result['status'] == 'unavailable'


def test_validation_rejects_misleading_comparison_in_snapshot_and_views():
    contract = compare([surface()], [surface(iv=.20524347)])
    manifest = {'synthesis_contract': {'option_comparison': contract}}
    metric = {'label': 'ATM implied volatility', 'comparison': 'rose versus previous headline'}
    for synthesis in ({'market_snapshot': [metric]}, {'top_views': [{'key_metrics': [metric]}]}):
        with pytest.raises(AnalysisValidationError, match='cross-expiry'):
            _check_option_comparisons(synthesis, manifest)
        metric['comparison'] = contract['comparison_text']
        _check_option_comparisons(synthesis, manifest)
        metric['comparison'] = 'rose versus previous headline'


def test_day_diff_exposes_constant_contract_comparison(tmp_path):
    from ccvm.analytics.monitor_state import build_day_diff
    futures = pa.table({'contract_code': ['GCZ26'], 'settlement': [4000.],
                        'front_back_slope': [1.], 'spread_to_next': [-1.]})
    class Store:
        def list_dates(self, *args):
            return ['2026-10-27', '2026-10-26']  # storage ordering is not assumed
        def exists(self, *args):
            return True
        def read(self, layer, dataset, dt):
            if dataset == 'futures_features':
                return futures
            return pa.Table.from_pylist([surface()] if dt == '2026-10-27' else
                                       [surface('2026-10-27', iv=.15), surface(iv=.20524347)])
    diff = build_day_diff(Store(), tmp_path, '2026-10-27')
    assert diff['prior_date'] == '2026-10-26'
    assert diff['atm_iv_change'] == pytest.approx(-.00355681)
    assert diff['option_comparison']['atm_iv_change_pp'] == pytest.approx(-.355681)
