"""Look up physical quantities backed by normalized standard source references."""

from .reference_data import _db, equal, parameter_tables, resolve_temperature_tick, result


def tables():
    return parameter_tables()


def lookup(table_number, symbol, axis_value):
    points = [
        dict(point)
        for point in _db().execute(
            'SELECT p.axis,c.* FROM parameter_point p '
            'JOIN parameter_definition d ON d.id=p.definition_id '
            'JOIN cell c ON c.id=p.cell_id WHERE d.table_number=? AND d.symbol=?',
            (table_number, symbol),
        )
    ]
    note = ''
    if symbol.startswith('πT') and isinstance(axis_value, (int, float)) and not isinstance(axis_value, bool):
        axis_value, note = resolve_temperature_tick(table_number, axis_value, [point['axis'] for point in points])
    matches = [(point, point) for point in points if equal(point['axis'], axis_value)]
    value, source = result(table_number, matches)
    return value, source + note
