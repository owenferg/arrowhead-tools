'''
pure python policy and validation for the GIUM arrow integration workflow

I intentionally didnt include an arcpy dependency so the GIUM field rules,
release names, and package checks can be tested in any Python runtime.
'''

from __future__ import annotations

import csv
import datetime as _datetime
import io
import math
import os
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Callable, Dict, Iterable, Mapping, Optional, Tuple


TEXT_FIELD_TYPES = frozenset({'String'})
INTEGER_FIELD_TYPES = frozenset({'SmallInteger', 'Integer', 'BigInteger'})
FLOAT_FIELD_TYPES = frozenset({'Single', 'Double'})
NUMERIC_FIELD_TYPES = INTEGER_FIELD_TYPES | FLOAT_FIELD_TYPES
DATE_FIELD_TYPES = frozenset({'Date', 'DateOnly', 'TimeOnly', 'TimestampOffset'})


@dataclass(frozen=True)
class FieldDefinition:
    '''field properties needed to validate an ArcGIS field'''

    name: str
    type: str
    length: Optional[int] = None


@dataclass(frozen=True)
class RoleProfile:
    '''accepted field aliases and required roles for one GIUM layer'''

    name: str
    aliases: Mapping[str, Tuple[str, ...]]
    required_roles: Tuple[str, ...]


# keep the aliases from the GIUM instructions visible even though matching ignores case
LINE_ROLE_PROFILE = RoleProfile(
    name='seasonal arrow lines',
    aliases={
        'herd': ('HerdName', 'Herd_Name'),
        'country': ('Country',),
        'season': ('Season',),
        'class': ('class', 'Class'),
    },
    required_roles=('herd', 'country', 'season', 'class'),
)

POINT_ROLE_PROFILE = RoleProfile(
    name='GIUM point labels',
    aliases={
        'herd': ('Herd_Name', 'HerdName'),
        'country': ('Country',),
        'season': ('Season',),
        'type': ('Type', 'TYPE'),
        'rotation': ('Rotation',),
    },
    required_roles=('herd', 'season', 'type', 'rotation'),
)


@dataclass(frozen=True)
class ReleaseArtifactNames:
    '''filenames created for one dated GIUM release'''

    line_shapefile: str
    line_zip: str
    point_shapefile: str
    point_geojson: str
    qa_csv: str

    def all(self) -> Tuple[str, ...]:
        '''return every filename in its normal release order'''

        return (
            self.line_shapefile,
            self.line_zip,
            self.point_shapefile,
            self.point_geojson,
            self.qa_csv,
        )


QA_COLUMNS = ('section', 'check', 'status', 'value', 'details')


@dataclass(frozen=True)
class QARow:
    '''one row in the GIUM release QA report'''

    section: str
    check: str
    status: str
    value: object = ''
    details: str = ''

    def as_dict(self) -> Dict[str, object]:
        '''convert the QA row into the columns expected by the CSV writer'''

        return {
            'section': self.section,
            'check': self.check,
            'status': self.status,
            'value': '' if self.value is None else self.value,
            'details': self.details,
        }


def field_definition(field: object) -> FieldDefinition:
    '''copy the properties used from an ArcGIS field'''

    if isinstance(field, FieldDefinition):
        return field
    name = str(getattr(field, 'name', '') or '').strip()
    field_type = str(getattr(field, 'type', '') or '').strip()
    if not name or not field_type:
        raise ValueError('Every field must have a name and ArcGIS field type')
    length = getattr(field, 'length', None)
    return FieldDefinition(name, field_type, length)


def resolve_role_fields(
    fields: Iterable[object],
    profile: RoleProfile,
    require_profile_roles: bool = True,
) -> Dict[str, Optional[FieldDefinition]]:
    '''match each GIUM role to one field without depending on capitalization

    required roles can be skipped for new data because the tool may add a field
    and fill it from the values entered by the user. ambiguous matches always fail.
    '''

    definitions = tuple(field_definition(field) for field in fields)
    resolved: Dict[str, Optional[FieldDefinition]] = {}
    used_fields = set()

    for role, aliases in profile.aliases.items():
        accepted = {alias.casefold() for alias in aliases}
        matches = [field for field in definitions if field.name.casefold() in accepted]
        if len(matches) > 1:
            names = ', '.join(sorted(field.name for field in matches))
            raise ValueError(
                f'Ambiguous {profile.name} field for {role}: {names}. '
                'Keep only one accepted alias.'
            )
        if not matches:
            if require_profile_roles and role in profile.required_roles:
                aliases_text = ', '.join(aliases)
                raise ValueError(
                    f'{profile.name.capitalize()} is missing the required {role} '
                    f'field (accepted names: {aliases_text})'
                )
            resolved[role] = None
            continue

        match = matches[0]
        key = match.name.casefold()
        if key in used_fields:
            raise ValueError(f'Field {match.name!r} resolves to more than one GIUM role')
        used_fields.add(key)
        resolved[role] = match

    return resolved


def is_blank(value: object) -> bool:
    '''check for null, empty, or whitespace-only values

    numeric zero and Boolean false count as populated values.
    '''

    return value is None or (isinstance(value, str) and not value.strip())


def coalesce_value(source_value: object, fallback_value: object) -> object:
    '''keep a populated source value or use the entered fallback'''

    return fallback_value if is_blank(source_value) else source_value


def validate_required_value(role: str, value: object, context: str = 'feature') -> None:
    '''make sure one required GIUM value is populated'''

    if is_blank(value):
        raise ValueError(f'{context.capitalize()} is missing required {role}')


def validate_required_values(
    values: Mapping[str, object],
    required_roles: Iterable[str],
    context: str = 'feature',
) -> None:
    '''report all missing required GIUM values together'''

    missing = [role for role in required_roles if is_blank(values.get(role))]
    if missing:
        raise ValueError(
            f'{context.capitalize()} is missing required values: {", ".join(missing)}'
        )


def validate_role_field(role: str, field: object) -> FieldDefinition:
    '''make sure a target field has the right type for its GIUM role'''

    definition = field_definition(field)
    if role == 'rotation':
        if definition.type not in NUMERIC_FIELD_TYPES:
            raise ValueError(
                f'Rotation field {definition.name!r} must be numeric, not '
                f'{definition.type}'
            )
    elif definition.type not in TEXT_FIELD_TYPES:
        raise ValueError(
            f'{role.capitalize()} field {definition.name!r} must be Text, not '
            f'{definition.type}'
        )
    return definition


def validate_value_for_field(
    value: object,
    field: object,
    role: Optional[str] = None,
    allow_blank: bool = True,
) -> object:
    '''validate a value without allowing ArcGIS to convert it'''

    definition = field_definition(field)
    label = role or definition.name
    if is_blank(value):
        if allow_blank:
            return value
        raise ValueError(f'{label.capitalize()} cannot be blank')

    if definition.type in TEXT_FIELD_TYPES:
        if not isinstance(value, str):
            raise ValueError(f'{label.capitalize()} must be text')
        if definition.length and len(value) > definition.length:
            raise ValueError(
                f'{label.capitalize()} value is {len(value)} characters but target '
                f'field {definition.name!r} allows {definition.length}'
            )
    elif definition.type in INTEGER_FIELD_TYPES:
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise ValueError(f'{label.capitalize()} must be an integer')
    elif definition.type in FLOAT_FIELD_TYPES:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f'{label.capitalize()} must be numeric')
        if not math.isfinite(float(value)):
            raise ValueError(f'{label.capitalize()} must be finite')
    elif definition.type in DATE_FIELD_TYPES:
        if not isinstance(value, (_datetime.date, _datetime.datetime, _datetime.time)):
            raise ValueError(f'{label.capitalize()} must be a date/time value')
    else:
        raise ValueError(
            f'Field {definition.name!r} has unsupported type {definition.type!r}'
        )
    return value


def validate_rotation(value: object, context: str = 'feature') -> float:
    '''return a numeric rotation in the production range from 0 to under 360'''

    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{context.capitalize()} rotation must be numeric')
    rotation = float(value)
    if not math.isfinite(rotation):
        raise ValueError(f'{context.capitalize()} rotation must be finite')
    if not 0.0 <= rotation < 360.0:
        raise ValueError(
            f'{context.capitalize()} rotation must be at least 0 and less than 360'
        )
    return rotation


def resolved_role_value(
    role: str,
    source_value: object,
    fallback_value: object,
    target_field: object,
    required: bool = True,
    context: str = 'feature',
) -> object:
    '''keep source data first, fill a blank, and validate the final value'''

    value = coalesce_value(source_value, fallback_value)
    if required:
        validate_required_value(role, value, context)
    validate_role_field(role, target_field)
    validate_value_for_field(value, target_field, role, allow_blank=not required)
    if role == 'rotation' and not is_blank(value):
        validate_rotation(value, context)
    return value


_MONTH_NAMES = (
    '',
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
)


def _parse_release_date(release_date: object) -> _datetime.date:
    '''convert an ArcGIS date or supported text date to a calendar date'''

    if isinstance(release_date, _datetime.datetime):
        return release_date.date()
    if isinstance(release_date, _datetime.date):
        return release_date
    if isinstance(release_date, str):
        value = release_date.strip()
        for date_format in ('%Y%m%d', '%Y-%m-%d'):
            try:
                return _datetime.datetime.strptime(value, date_format).date()
            except ValueError:
                pass
    raise ValueError('Release date must be a date or YYYYMMDD/ YYYY-MM-DD text')


def release_stamp(release_date: object) -> str:
    '''build a locale-stable MonthDay_Year stamp such as June10_2026'''

    date = _parse_release_date(release_date)
    return f'{_MONTH_NAMES[date.month]}{date.day}_{date.year}'


def release_artifact_names(release_date: object) -> ReleaseArtifactNames:
    '''build the standard filenames for a GIUM release date'''

    stamp = release_stamp(release_date)
    return ReleaseArtifactNames(
        line_shapefile=f'SeasonalArrowsMerged_{stamp}.shp',
        line_zip=f'SeasonalArrowsMerged_{stamp}.zip',
        point_shapefile=f'GIUMPointLabelsMerged_{stamp}.shp',
        point_geojson=f'GIUMPointLabelsMerged_{stamp}.geojson',
        qa_csv=f'GIUMArrowIntegration_{stamp}_QA.csv',
    )


def release_artifact_paths(output_folder: object, release_date: object) -> Dict[str, str]:
    '''join the release filenames to a folder without creating anything'''

    folder = str(output_folder or '').strip()
    if not folder:
        raise ValueError('Output folder is required')
    names = release_artifact_names(release_date)
    return {
        'line_shapefile': os.path.join(folder, names.line_shapefile),
        'line_zip': os.path.join(folder, names.line_zip),
        'point_shapefile': os.path.join(folder, names.point_shapefile),
        'point_geojson': os.path.join(folder, names.point_geojson),
        'qa_csv': os.path.join(folder, names.qa_csv),
    }


def find_artifact_collisions(
    paths: Iterable[object],
    exists: Callable[[object], bool] = os.path.exists,
) -> Tuple[str, ...]:
    '''find release paths that already exist'''

    return tuple(str(path) for path in paths if exists(path))


def ensure_no_artifact_collisions(
    paths: Iterable[object],
    exists: Callable[[object], bool] = os.path.exists,
) -> None:
    '''stop before ArcGIS can overwrite an existing release'''

    collisions = find_artifact_collisions(paths, exists)
    if collisions:
        raise ValueError('Release output already exists: ' + ', '.join(collisions))


def qa_csv_text(rows: Iterable[QARow]) -> str:
    '''write QA rows with the same columns every time'''

    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=QA_COLUMNS, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        if not isinstance(row, QARow):
            raise TypeError('QA rows must be QARow instances')
        writer.writerow(row.as_dict())
    return output.getvalue()


_REQUIRED_SHAPEFILE_SUFFIXES = ('.shp', '.shx', '.dbf', '.prj')
_OPTIONAL_SHAPEFILE_SUFFIXES = ('.cpg', '.sbn', '.sbx', '.xml', '.shp.xml')


def select_shapefile_zip_members(
    paths: Iterable[object],
    shapefile_name: object,
) -> Tuple[str, ...]:
    '''choose the shapefile sidecars that belong in the line ZIP

    paths are returned in a consistent order and written at the root of the ZIP.
    '''

    requested = os.path.basename(str(shapefile_name))
    stem, extension = os.path.splitext(requested)
    if extension.casefold() != '.shp' or not stem:
        raise ValueError('Shapefile name must end in .shp')

    suffixes = _REQUIRED_SHAPEFILE_SUFFIXES + _OPTIONAL_SHAPEFILE_SUFFIXES
    found: Dict[str, str] = {}
    for path_value in paths:
        path = str(path_value)
        filename = os.path.basename(path)
        filename_folded = filename.casefold()
        for suffix in suffixes:
            if filename_folded == (stem + suffix).casefold():
                key = suffix.casefold()
                if key in found:
                    raise ValueError(f'Duplicate shapefile sidecar for {stem + suffix}')
                found[key] = path

    missing = [suffix for suffix in _REQUIRED_SHAPEFILE_SUFFIXES if suffix not in found]
    if missing:
        raise ValueError(
            f'Shapefile package is missing required files: '
            f'{", ".join(stem + suffix for suffix in missing)}'
        )
    return tuple(found[suffix] for suffix in suffixes if suffix in found)
