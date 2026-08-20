'''
pure python policy and validation for the GIUM data integration workflow

I intentionally didnt include an arcpy dependency so the GIUM field rules,
release names, and package checks can be tested in any Python runtime.
'''

from __future__ import annotations

import csv
import datetime as _datetime
import io
import math
import os
import re
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Tuple


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


# the accepted spellings for every role the tool understands. matching ignores
# case, but the variants seen in production are kept visible because they are
# what the operator sees in the "accepted names" half of an error message.
ROLE_ALIASES: Dict[str, Tuple[str, ...]] = {
    'herd': ('Herd_Name', 'HerdName'),
    'country': ('Country',),
    'season': ('Season',),
    'class': ('Class', 'class'),
    'type': ('Type', 'type'),
    'rotation': ('Rotation',),
    'source': ('Source',),
    'locale': ('Locale',),
    'migrate_id': ('Migrate_ID', 'MigrateID'),
}

# roles the tool fills from a value typed into the form, so their target field
# has to hold that kind of value. every other role is only ever carried across
# from the new data, so its field type is whatever production already uses.
TEXT_ROLES = frozenset({'herd', 'country', 'season', 'class', 'type'})
NUMERIC_ROLES = frozenset({'rotation'})

PACKAGE_ZIP = 'Zipped shapefile'
PACKAGE_GEOJSON = 'GeoJSON'
PACKAGE_BOTH = 'Both'
PACKAGE_CHOICES = (PACKAGE_ZIP, PACKAGE_GEOJSON, PACKAGE_BOTH)
SUPPORTED_SHAPE_TYPES = ('Point', 'Polyline', 'Polygon')

_PACKAGE_FORMATS: Dict[str, Tuple[str, ...]] = {
    PACKAGE_ZIP.casefold(): ('zip',),
    PACKAGE_GEOJSON.casefold(): ('geojson',),
    PACKAGE_BOTH.casefold(): ('zip', 'geojson'),
}

# the columns of the datasets value table, in the order the tool form shows them
DATASET_COLUMNS = (
    'layer_type',
    'target',
    'new_data',
    'class',
    'type',
    'season',
    'package',
    'transformation',
)


@dataclass(frozen=True)
class LayerProfile:
    '''field rules, output naming, and packaging for one kind of GIUM layer

    shape_type of None accepts any supported geometry, and output_base of None
    means the release is named after the chosen production target.
    '''

    name: str
    shape_type: Optional[str]
    aliases: Mapping[str, Tuple[str, ...]]
    required_roles: Tuple[str, ...]
    output_base: Optional[str]
    default_package: str


def _role_aliases(*roles: str) -> Dict[str, Tuple[str, ...]]:
    '''take the shared alias spellings for the roles one layer type uses'''

    return {role: ROLE_ALIASES[role] for role in roles}


OTHER_PROFILE_NAME = 'Other'

LAYER_PROFILES: Tuple[LayerProfile, ...] = (
    LayerProfile(
        name='Seasonal arrows',
        shape_type='Polyline',
        aliases=_role_aliases('herd', 'country', 'season', 'class'),
        required_roles=('herd', 'country', 'season', 'class'),
        output_base='SeasonalArrowsMerged',
        default_package=PACKAGE_ZIP,
    ),
    LayerProfile(
        name='GIUM point labels',
        shape_type='Point',
        aliases=_role_aliases('herd', 'country', 'season', 'type', 'rotation'),
        required_roles=('herd', 'season', 'type', 'rotation'),
        output_base='GIUMPointLabelsMerged',
        default_package=PACKAGE_GEOJSON,
    ),
    LayerProfile(
        name='Linear barriers',
        shape_type='Polyline',
        aliases=_role_aliases(
            'class', 'herd', 'country', 'source', 'locale', 'migrate_id'
        ),
        required_roles=('class', 'herd', 'country'),
        output_base='linear_barriers',
        default_package=PACKAGE_ZIP,
    ),
    LayerProfile(
        name='Point barriers',
        shape_type='Point',
        aliases=_role_aliases(
            'class', 'herd', 'country', 'source', 'locale', 'migrate_id'
        ),
        required_roles=('class', 'herd', 'country'),
        output_base='point_barriers',
        default_package=PACKAGE_ZIP,
    ),
    LayerProfile(
        name='Polygon features',
        shape_type='Polygon',
        aliases=_role_aliases(
            'class', 'herd', 'country', 'type', 'source', 'locale', 'migrate_id'
        ),
        required_roles=('class', 'herd', 'country'),
        output_base='poly_features',
        default_package=PACKAGE_ZIP,
    ),
    LayerProfile(
        name='Protected areas',
        shape_type='Polygon',
        aliases=_role_aliases('type', 'herd', 'country'),
        required_roles=('type', 'herd'),
        output_base='ProtectedAreas',
        default_package=PACKAGE_ZIP,
    ),
    LayerProfile(
        name='Line labels',
        shape_type='Polyline',
        aliases=_role_aliases('herd', 'country', 'season', 'type', 'class'),
        required_roles=('herd',),
        output_base=None,
        default_package=PACKAGE_ZIP,
    ),
    LayerProfile(
        name=OTHER_PROFILE_NAME,
        shape_type=None,
        aliases=_role_aliases(*ROLE_ALIASES),
        required_roles=(),
        output_base=None,
        default_package=PACKAGE_ZIP,
    ),
)

LAYER_PROFILE_NAMES: Tuple[str, ...] = tuple(
    profile.name for profile in LAYER_PROFILES
)

_LAYER_PROFILES_BY_NAME = {
    profile.name.casefold(): profile for profile in LAYER_PROFILES
}


def layer_profile(name: object) -> LayerProfile:
    '''look up one layer type without depending on capitalization'''

    key = str(name or '').strip().casefold()
    profile = _LAYER_PROFILES_BY_NAME.get(key)
    if profile is None:
        raise ValueError(
            f'Unknown layer type {str(name or "").strip()!r}. Choose one of: '
            + ', '.join(LAYER_PROFILE_NAMES)
        )
    return profile


def resolve_package_formats(package: object, profile: LayerProfile) -> Tuple[str, ...]:
    '''turn the packaging choice into the formats to write for one dataset

    a blank choice keeps the default the GIUM instructions use for that layer.
    '''

    requested = str(package or '').strip()
    if not requested:
        requested = profile.default_package
    formats = _PACKAGE_FORMATS.get(requested.casefold())
    if formats is None:
        raise ValueError(
            f'Unknown package format {requested!r}. Choose one of: '
            + ', '.join(PACKAGE_CHOICES)
        )
    return formats


def parse_dataset_row(row: object, index: int = 1) -> Dict[str, object]:
    '''normalize one datasets-table row into the named columns'''

    if isinstance(row, Mapping):
        values = {column: row.get(column) for column in DATASET_COLUMNS}
    else:
        try:
            items = list(row)
        except TypeError:
            raise ValueError(f'Row {index} is not a valid datasets-table row.') from None
        if len(items) < 3:
            raise ValueError(
                f'Row {index} is missing layer type, target, or new data.'
            )
        while len(items) < len(DATASET_COLUMNS):
            items.append(None)
        values = dict(zip(DATASET_COLUMNS, items[: len(DATASET_COLUMNS)]))
    if is_blank(values.get('layer_type')):
        raise ValueError(f'Row {index} needs a layer type.')
    if is_blank(values.get('target')):
        raise ValueError(f'Row {index} needs an existing production shapefile.')
    if is_blank(values.get('new_data')):
        raise ValueError(f'Row {index} needs the new data to add.')
    return values


def parse_dataset_rows(datasets: object) -> List[Dict[str, object]]:
    '''normalize every datasets-table row, or an empty list when none were given'''

    if not datasets:
        return []
    return [
        parse_dataset_row(row, index) for index, row in enumerate(datasets, start=1)
    ]


def present_roles(
    resolved: Mapping[str, Optional[FieldDefinition]],
) -> Tuple[str, ...]:
    '''return the profile roles that actually exist on a dataset'''

    return tuple(role for role, field in resolved.items() if field is not None)


def enforced_roles(
    profile: LayerProfile,
    resolved: Mapping[str, Optional[FieldDefinition]],
) -> Tuple[str, ...]:
    '''return required roles that the chosen target actually has

    a profile never demands a column the production shapefile does not contain.
    '''

    return tuple(role for role in profile.required_roles if resolved.get(role))


@dataclass(frozen=True)
class DatasetArtifactNames:
    '''filenames created for one dataset in a dated GIUM release'''

    shapefile: str
    zip: Optional[str] = None
    geojson: Optional[str] = None

    def all(self) -> Tuple[str, ...]:
        '''return every filename in its normal release order'''

        return tuple(
            name for name in (self.shapefile, self.zip, self.geojson) if name
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
    profile: LayerProfile,
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
                    f'{profile.name} is missing the required {role} '
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
    '''make sure a target field has the right type for its GIUM role

    only roles the form can fill are constrained. a role such as source or
    migrate_id is carried across from the new data untouched, so production is
    free to store it in whichever type it already uses.
    '''

    definition = field_definition(field)
    if role in NUMERIC_ROLES:
        if definition.type not in NUMERIC_FIELD_TYPES:
            raise ValueError(
                f'{role.capitalize()} field {definition.name!r} must be numeric, not '
                f'{definition.type}'
            )
    elif role in TEXT_ROLES and definition.type not in TEXT_FIELD_TYPES:
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


_RELEASE_STAMP_PATTERN = re.compile(
    r'_(?:' + '|'.join(_MONTH_NAMES[1:]) + r')\d{1,2}_\d{4}$',
    re.IGNORECASE,
)


def strip_release_stamp(name: object) -> str:
    '''drop a trailing MonthDay_Year stamp so a new one can be added

    production targets are already dated, so naming a release after its target
    would otherwise stack one stamp on top of another.
    '''

    base = os.path.basename(str(name or '').strip())
    base = os.path.splitext(base)[0]
    return _RELEASE_STAMP_PATTERN.sub('', base)


def dataset_base_name(
    profile: LayerProfile,
    release_date: object,
    target_name: object = None,
) -> str:
    '''build the dated output name for one dataset in a release'''

    base = profile.output_base or strip_release_stamp(target_name)
    if not base:
        raise ValueError(
            f'The {profile.name} layer type is named after its production target, '
            'so a target shapefile name is required.'
        )
    return f'{base}_{release_stamp(release_date)}'


def release_artifact_names(
    profile: LayerProfile,
    release_date: object,
    package: object = None,
    target_name: object = None,
) -> DatasetArtifactNames:
    '''build the filenames one dataset contributes to a GIUM release'''

    base = dataset_base_name(profile, release_date, target_name)
    formats = resolve_package_formats(package, profile)
    return DatasetArtifactNames(
        shapefile=f'{base}.shp',
        zip=f'{base}.zip' if 'zip' in formats else None,
        geojson=f'{base}.geojson' if 'geojson' in formats else None,
    )


def release_artifact_paths(
    output_folder: object,
    profile: LayerProfile,
    release_date: object,
    package: object = None,
    target_name: object = None,
) -> Dict[str, str]:
    '''join one dataset's release filenames to a folder without creating anything'''

    folder = _required_folder(output_folder)
    names = release_artifact_names(profile, release_date, package, target_name)
    paths = {'shapefile': os.path.join(folder, names.shapefile)}
    if names.zip:
        paths['zip'] = os.path.join(folder, names.zip)
    if names.geojson:
        paths['geojson'] = os.path.join(folder, names.geojson)
    return paths


def qa_report_name(release_date: object) -> str:
    '''name the single QA report that covers every dataset in a release'''

    return f'GIUMIntegration_{release_stamp(release_date)}_QA.csv'


def qa_report_path(output_folder: object, release_date: object) -> str:
    '''join the QA report name to a folder without creating anything'''

    return os.path.join(_required_folder(output_folder), qa_report_name(release_date))


def _required_folder(output_folder: object) -> str:
    '''reject a blank output folder before any path is built from it'''

    folder = str(output_folder or '').strip()
    if not folder:
        raise ValueError('Output folder is required')
    return folder


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
    '''choose the shapefile sidecars that belong in a release ZIP

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
