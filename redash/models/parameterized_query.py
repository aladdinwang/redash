import pystache
from functools import partial
from numbers import Number
from redash.utils import mustache_render, json_loads
from redash.permissions import require_access, view_only
from funcy import distinct
from dateutil.parser import parse

from six import string_types, text_type


def _pluck_name_and_value(default_column, row):
    row = {k.lower(): v for k, v in row.items()}
    name_column = "name" if "name" in row.keys() else default_column.lower()
    value_column = "value" if "value" in row.keys() else default_column.lower()

    return {"name": row[name_column], "value": text_type(row[value_column])}


def _load_result(query_id, org):
    from redash import models

    query = models.Query.get_by_id_and_org(query_id, org)

    if query.data_source:
        query_result = models.QueryResult.get_by_id_and_org(query.latest_query_data_id, org)
        return json_loads(query_result.data)
    else:
        raise QueryDetachedFromDataSourceError(query_id)


def dropdown_values(query_id, org):
    data = _load_result(query_id, org)
    first_column = data["columns"][0]["name"]
    pluck = partial(_pluck_name_and_value, first_column)
    return map(pluck, data["rows"])


def join_parameter_list_values(parameters, schema):
    updated_parameters = {}
    for (key, value) in parameters.items():
        if isinstance(value, list):
            definition = next((definition for definition in schema if definition["name"] == key), {})
            multi_values_options = definition.get('multiValuesOptions', {})
            separator = str(multi_values_options.get('separator', ','))
            prefix = str(multi_values_options.get('prefix', ''))
            suffix = str(multi_values_options.get('suffix', ''))
            updated_parameters[key] = separator.join(map(lambda v: prefix + v + suffix, value))
        else:
            updated_parameters[key] = value
    return updated_parameters


def _collect_key_names(nodes):
    keys = []
    for node in nodes._parse_tree:
        if isinstance(node, pystache.parser._EscapeNode):
            keys.append(node.key)
        elif isinstance(node, pystache.parser._SectionNode):
            keys.append(node.key)
            keys.extend(_collect_key_names(node.parsed))

    return distinct(keys)


def _collect_query_parameters(query):
    nodes = pystache.parse(query)
    keys = _collect_key_names(nodes)
    return keys


def _parameter_names(parameter_values):
    names = []
    for key, value in parameter_values.items():
        if isinstance(value, dict):
            for inner_key in value.keys():
                names.append(u'{}.{}'.format(key, inner_key))
        else:
            names.append(key)

    return names


def _is_number(string):
    if isinstance(string, Number):
        return True
    else:
        try:
            float(string)
            return True
        except ValueError:
            return False


def _is_date(string):
    try:
        parse(string)
        return True
    except (ValueError, TypeError):
        return False


def _is_date_range(obj):
    try:
        return _is_date(obj["start"]) and _is_date(obj["end"])
    except (KeyError, TypeError):
        return False


def _is_value_within_options(value, dropdown_options, allow_list=False):
    if isinstance(value, list):
        return allow_list and set(map(text_type, value)).issubset(set(dropdown_options))
    return text_type(value) in dropdown_options


class ParameterizedQuery(object):
    def __init__(self, template, schema=None, org=None):
        self.schema = schema or []
        self.org = org
        self.template = template
        self.query = template
        self.parameters = {}

    def apply(self, parameters):
        invalid_parameter_names = [key for (key, value) in parameters.items() if not self._valid(key, value)]
        if invalid_parameter_names:
            raise InvalidParameterError(invalid_parameter_names)
        else:
            self.parameters.update(parameters)
            self.query = mustache_render(self.template, join_parameter_list_values(parameters, self.schema))

        return self

    def _valid(self, name, value):
        if not self.schema:
            return True

        definition = next((definition for definition in self.schema if definition["name"] == name), None)

        if not definition:
            return False

        enum_options = definition.get('enumOptions')
        query_id = definition.get('queryId')
        allow_multiple_values = isinstance(definition.get('multiValuesOptions'), dict)

        if isinstance(enum_options, string_types):
            enum_options = enum_options.split('\n')

        validators = {
            "text": lambda value: isinstance(value, string_types),
            "number": _is_number,
            "enum": lambda value: _is_value_within_options(value,
                                                           enum_options,
                                                           allow_multiple_values),
            "query": lambda value: _is_value_within_options(value,
                                                            [v["value"] for v in dropdown_values(query_id, self.org)],
                                                            allow_multiple_values),
            "date": _is_date,
            "datetime-local": _is_date,
            "datetime-with-seconds": _is_date,
            "date-range": _is_date_range,
            "datetime-range": _is_date_range,
            "datetime-range-with-seconds": _is_date_range,
        }

        validate = validators.get(definition["type"], lambda x: False)

        return validate(value)

    @property
    def is_safe(self):
        text_parameters = filter(lambda p: p["type"] == "text", self.schema)
        return not any(text_parameters)

    @property
    def missing_params(self):
        query_parameters = set(_collect_query_parameters(self.template))
        return set(query_parameters) - set(_parameter_names(self.parameters))

    @property
    def text(self):
        return self.query


class InvalidParameterError(Exception):
    def __init__(self, parameters):
        parameter_names = u", ".join(parameters)
        message = u"The following parameter values are incompatible with their definitions: {}".format(parameter_names)
        super(InvalidParameterError, self).__init__(message)


class QueryDetachedFromDataSourceError(Exception):
    def __init__(self, query_id):
        self.query_id = query_id
        super(QueryDetachedFromDataSourceError, self).__init__(
            "This query is detached from any data source. Please select a different query.")
