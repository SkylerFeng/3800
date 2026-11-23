import pytest
from pydantic import ValidationError
from app.config import Settings
from app.domain import DistanceInput, DistanceSample
from app.warning import WarningRules


def sample(distance=None, status='valid', received=100):
    return DistanceSample(DistanceInput(status=status, distance_m=distance), 'test', received)


@pytest.mark.parametrize('distance,level', [
    (.02, 'critical'), (.30, 'critical'), (.3001, 'warning'), (.50, 'warning'),
    (.5001, 'notice'), (.80, 'notice'), (.8001, 'normal'), (20, 'normal'),
])
def test_boundaries(distance,level):
    assert WarningRules(Settings()).evaluate(sample(distance),100)['level']==level


def test_hysteresis():
    rules = WarningRules(Settings(hysteresis_m=.02))
    distances = [.30, .31, .33, .51, .53, .81, .83, .79, .49, .29]
    assert [rules.evaluate(sample(d), 100)['level'] for d in distances] == [
        'critical', 'critical', 'warning', 'warning', 'notice', 'notice', 'normal',
        'notice', 'warning', 'critical',
    ]


@pytest.mark.parametrize('status',['no_target','stale','fault','not_installed','unconfigured'])
def test_invalid_is_unknown(status):
    result=WarningRules(Settings()).evaluate(sample(status=status),100)
    assert result['level']=='unknown'
    assert '不能' in result['message']


def test_natural_stale():
    r=WarningRules(Settings())
    assert r.evaluate(sample(.2),103)['status']=='valid'
    assert r.evaluate(sample(.2),103)['alert_remaining_s']==0
    assert r.evaluate(sample(.2),103.001)['status']=='stale'


@pytest.mark.parametrize('value',[0,-1,21,float('nan'),float('inf'),None])
def test_bad_distance(value):
    with pytest.raises(ValidationError): DistanceInput(distance_m=value)


def test_inconsistent_distance():
    with pytest.raises(ValidationError): DistanceInput(status='fault',distance_m=1)


@pytest.mark.parametrize('kwargs',[{'critical_m':2},{'model_timeout_s':0},{'distance_ttl_s':float('nan')},{'vision_provider':'unknown'}])
def test_config_validation(kwargs):
    with pytest.raises(ValueError): Settings(**kwargs)
