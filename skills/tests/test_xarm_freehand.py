"""xArm catalog discovery and device-advertised motion availability."""
import pytest
from pydantic import ValidationError
from lab_skills.models import EquipmentStatus
from lab_skills.session import _availability
from lab_skills.skill_catalog import skills_for


def definitions():
    return {d.name: d for d in skills_for('robot_arm', 'xarm_translocation')}


def test_freehand_is_xarm_specific():
    names = {'freehand.position', 'freehand.relative', 'freehand.joints'}
    assert names <= definitions().keys()
    for device in (None, 'dobot_mg400', 'ligand_ur5e'):
        assert names.isdisjoint(d.name for d in skills_for('robot_arm', device))


@pytest.mark.parametrize('name,body', [
    ('position', {'x': 100, 'y': 20, 'z': 200}),
    ('relative', {'dz': 2}),
    ('joints', {'angles': [0, 0, 0, 0, 0]}),
])
def test_freehand_schema_and_availability(name, body):
    skill = definitions()[f'freehand.{name}']
    assert skill.endpoint == f'/control/freehand/{name}'
    assert skill.method == 'POST'
    parsed = skill.args_schema.model_validate(body).model_dump(exclude_none=True)
    assert all(parsed[key] == value for key, value in body.items())
    status = EquipmentStatus(
        equipment_id='xarm_translocation', equipment_kind='robot_arm',
        equipment_status='ready', equipment_name='xArm',
        device_time='2026-09-22T00:00:00Z',
        allowed_actions=['stop', skill.name],
    )
    assert _availability(skill, status, None, None)[0]
    # STRICT or running: device withholds the action even if state is ready.
    status.allowed_actions = ['stop']
    assert not _availability(skill, status, None, None)[0]
    with pytest.raises(ValidationError):
        skill.args_schema.model_validate({**body, 'typo': 1})


def test_graph_mode_preserves_override_fields():
    schema = definitions()['graph.mode'].args_schema
    body = {'mode': 'off', 'reason': 'Supervised Cartesian work', 'ttl_seconds': 300}
    assert schema.model_validate(body).model_dump() == body
    assert schema.model_validate({'mode': 'strict'}).mode == 'strict'
    with pytest.raises(ValidationError):
        schema.model_validate({**body, 'ttl_seconds': 0})
