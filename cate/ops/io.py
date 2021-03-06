# The MIT License (MIT)
# Copyright (c) 2016, 2017 by the ESA CCI Toolbox development team and contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import itertools
import json
import os.path
import urllib.parse
import urllib.request
from abc import ABCMeta
from typing import List, Optional

import fiona
import geopandas as gpd
import pandas as pd
import pandas.api.types
import s3fs
import xarray as xr

from cate.core.ds import get_spatial_ext_chunk_sizes
from cate.core.objectio import OBJECT_IO_REGISTRY, ObjectIO
from cate.core.op import OP_REGISTRY, op_input, op
from cate.core.types import VarNamesLike, TimeRangeLike, PolygonLike, DictLike, FileLike, GeoDataFrame, DataFrameLike, \
    ValidationError
from cate.ops.normalize import adjust_temporal_attrs
from cate.ops.normalize import normalize as normalize_op
from cate.util.monitor import Monitor

_ALL_FILE_FILTER = dict(name='All Files', extensions=['*'])


@op(tags=['input'], res_pattern='ds_{index}')
@op_input('ds_id', nullable=False)
@op_input('ds_name', nullable=False, deprecated='use "ds_id" instead')
@op_input('time_range', data_type=TimeRangeLike)
@op_input('region', data_type=PolygonLike)
@op_input('var_names', data_type=VarNamesLike)
@op_input('normalize')
@op_input('force_local')
@op_input('local_ds_id')
def open_dataset(ds_name: str = '',
                 ds_id: str = '',
                 time_range: TimeRangeLike.TYPE = None,
                 region: PolygonLike.TYPE = None,
                 var_names: VarNamesLike.TYPE = None,
                 normalize: bool = True,
                 force_local: bool = False,
                 local_ds_id: str = None,
                 monitor: Monitor = Monitor.NONE) -> xr.Dataset:
    """
    Open a dataset from a data source identified by *ds_name*.

    :param ds_name: The name of data source. This parameter has been deprecated, please use *ds_id* instead.
    :param ds_id: The identifier for the data source.
    :param time_range: Optional time range of the requested dataset
    :param region: Optional spatial region of the requested dataset
    :param var_names: Optional names of variables of the requested dataset
    :param normalize: Whether to normalize the dataset's geo- and time-coding upon opening. See operation ``normalize``.
    :param force_local: Whether to make a local copy of remote data source if it's not present
    :param local_ds_id: Optional local identifier for newly created local copy of remote data source.
           Used only if force_local=True.
    :param monitor: A progress monitor
    :return: An new dataset instance.
    """
    import cate.core.ds
    ds = cate.core.ds.open_dataset(data_source=ds_id or ds_name,
                                   time_range=time_range,
                                   var_names=var_names,
                                   region=region,
                                   force_local=force_local,
                                   local_ds_id=local_ds_id,
                                   monitor=monitor)
    if ds and normalize:
        return adjust_temporal_attrs(normalize_op(ds))

    return ds


# noinspection PyShadowingBuiltins
@op(tags=['output'], no_cache=True)
@op_input('ds')
@op_input('file', file_open_mode='w', file_filters=[dict(name='NetCDF', extensions=['nc']), _ALL_FILE_FILTER])
@op_input('format', value_set=['NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT', 'NETCDF3_CLASSIC'])
def save_dataset(ds: xr.Dataset, file: str, format: str = None, monitor: Monitor = Monitor.NONE):
    """
    Save a dataset to NetCDF file.

    :param ds: The dataset
    :param file: File path
    :param format: NetCDF format flavour, one of 'NETCDF4', 'NETCDF4_CLASSIC', 'NETCDF3_64BIT', 'NETCDF3_CLASSIC'.
    :param monitor: a progress monitor.
    """
    with monitor.observing("save_dataset"):
        ds.to_netcdf(file, format=format)


# noinspection PyShadowingBuiltins
@op(tags=['input'])
@op_input('file', file_open_mode='r')
@op_input('format')
def read_object(file: str, format: str = None) -> object:
    """
    Read a data object from a file.

    :param file: The file path.
    :param format: Optional format name.
    :return: The data object.
    """
    import cate.core.objectio
    obj, _ = cate.core.objectio.read_object(file, format_name=format)
    return obj


# noinspection PyShadowingBuiltins
@op(tags=['output'], no_cache=True)
@op_input('obj')
@op_input('file', file_open_mode='w', file_filters=[_ALL_FILE_FILTER])
@op_input('format')
def write_object(obj, file: str, format: str = None):
    """
    Write a data object to a file.

    :param obj: The object to write.
    :param file: The file path.
    :param format: Optional format name.
    :return: The data object.
    """
    import cate.core.objectio
    cate.core.objectio.write_object(obj, file, format_name=format)


@op(tags=['input'], res_pattern='txt_{index}')
@op_input('file', file_open_mode='r', file_filters=[dict(name='Plain Text', extensions=['txt']), _ALL_FILE_FILTER])
@op_input('encoding')
def read_text(file: str, encoding: str = None) -> str:
    """
    Read a string object from a text file.

    :param file: The text file path.
    :param encoding: Optional encoding, e.g. "utc-8".
    :return: The string object.
    """
    if isinstance(file, str):
        with open(file, 'r', encoding=encoding) as fp:
            return fp.read()
    else:
        # noinspection PyUnresolvedReferences
        return file.read()


@op(tags=['output'], no_cache=True)
@op_input('obj')
@op_input('file', file_open_mode='w', file_filters=[dict(name='Plain Text', extensions=['txt']), _ALL_FILE_FILTER])
@op_input('encoding')
def write_text(obj: object, file: str, encoding: str = None):
    """
    Write an object as string to a text file.

    :param obj: The data object.
    :param file: The text file path.
    :param encoding: Optional encoding, e.g. "utc-8".
    """
    if isinstance(file, str):
        with open(file, 'w', encoding=encoding) as fp:
            fp.write(str(obj))
    else:
        # noinspection PyUnresolvedReferences
        file.write(str(obj))


@op(tags=['input'])
@op_input('file', file_open_mode='r', file_filters=[dict(name='JSON', extensions=['json']), _ALL_FILE_FILTER])
@op_input('encoding')
def read_json(file: str, encoding: str = None) -> object:
    """
    Read a data object from a JSON text file.

    :param file: The JSON file path.
    :param encoding: Optional encoding, e.g. "utc-8".
    :return: The data object.
    """
    if isinstance(file, str):
        with open(file, 'r', encoding=encoding) as fp:
            return json.load(fp)
    else:
        return json.load(file)


@op(tags=['output'], no_cache=True)
@op_input('obj')
@op_input('file', file_open_mode='w', file_filters=[dict(name='JSON', extensions=['json']), _ALL_FILE_FILTER])
@op_input('encoding')
@op_input('indent')
def write_json(obj: object, file: str, encoding: str = None, indent: str = None):
    """
    Write a data object to a JSON text file. Note that the data object must be JSON-serializable.

    :param obj: A JSON-serializable data object.
    :param file: The JSON file path.
    :param encoding: Optional encoding, e.g. "utf-8".
    :param indent: indent used in the file, e.g. "  " (two spaces).
    """
    if isinstance(file, str):
        with open(file, 'w', encoding=encoding) as fp:
            json.dump(obj, fp, indent=indent)
    else:
        json.dump(obj, file, indent=indent)


@op(tags=['input'], res_pattern='df_{index}')
@op_input('file',
          data_type=FileLike,
          file_open_mode='r',
          file_filters=[dict(name='CSV', extensions=['csv', 'txt']), _ALL_FILE_FILTER])
@op_input('delimiter', nullable=True)
@op_input('delim_whitespace', nullable=True)
@op_input('quotechar', nullable=True)
@op_input('comment', nullable=True)
@op_input('index_col', nullable=True)
@op_input('parse_points', nullable=True)
@op_input('more_args', nullable=True, data_type=DictLike)
def read_csv(file: FileLike.TYPE,
             delimiter: str = ',',
             delim_whitespace: bool = False,
             quotechar: str = None,
             comment: str = None,
             index_col: str = None,
             parse_points: bool = True,
             more_args: DictLike.TYPE = None) -> pd.DataFrame:
    """
    Read comma-separated values (CSV) from plain text file into a Pandas DataFrame.

    :param file: The CSV file path.
    :param delimiter: Delimiter to use. If delimiter is None, will try to automatically determine this.
    :param delim_whitespace: Specifies whether or not whitespaces will be used as delimiter.
           If this option is set, nothing should be passed in for the delimiter parameter.
    :param quotechar: The character used to denote the start and end of a quoted item.
           Quoted items can include the delimiter and it will be ignored.
    :param comment: Indicates remainder of line should not be parsed.
           If found at the beginning of a line, the line will be ignored altogether.
           This parameter must be a single character.
    :param index_col: The name of the column that provides unique identifiers
    :param parse_points: If set and if longitude and latitude columns are present,
           generate a column "geometry" comprising spatial points. Result is a GeoPandas
           GeoDataFrame in this case.
    :param more_args: Other optional keyword arguments.
           Please refer to Pandas documentation of ``pandas.read_csv()`` function.
    :return: The DataFrame object.
    """
    # The following code is needed, because Pandas treats any kw given in kwargs as being set, even if just None.
    kwargs = DictLike.convert(more_args)
    if kwargs is None:
        kwargs = {}
    if delimiter:
        kwargs.update(delimiter=delimiter)
    if delim_whitespace:
        kwargs.update(delim_whitespace=delim_whitespace)
    if quotechar:
        kwargs.update(quotechar=quotechar)
    if comment:
        kwargs.update(comment=comment)
    if index_col:
        kwargs.update(index_col=index_col)
    data_frame = pd.read_csv(file, **kwargs)
    try:
        if data_frame.index.name in ('date', 'time'):
            # Try to coerce the index column into datetime objects required to work
            # with the time-series data
            data_frame.index = pd.to_datetime(data_frame.index)
    except Exception:
        # We still want to use the data
        pass

    if parse_points:
        col_names: List[str] = list(data_frame.columns)
        col_names_lc: List[str] = list(map(lambda n: n.lower(), col_names))

        def col_ok(name: str) -> Optional[str]:
            name_lc = name.lower()
            if name_lc in col_names_lc:
                i = col_names_lc.index(name_lc)
                col_name = col_names[i]
                return col_name if pandas.api.types.is_numeric_dtype(data_frame[col_name].dtype) else None
            return None

        lon_name = col_ok('lon') or col_ok('long') or col_ok('longitude')
        lat_name = col_ok('lat') or col_ok('latitude')
        if lon_name and lat_name:
            data_frame = gpd.GeoDataFrame(data_frame,
                                          geometry=gpd.points_from_xy(data_frame[lon_name], data_frame[lat_name]))

    return data_frame


@op(tags=['input'], res_pattern='df_{index}', no_cache=True)
@op_input('obj', data_type=DataFrameLike)
@op_input('file',
          data_type=FileLike,
          file_open_mode='w',
          file_filters=[dict(name='CSV', extensions=['csv', 'txt']), _ALL_FILE_FILTER])
@op_input('columns', value_set_source='obj', data_type=VarNamesLike)
@op_input('na_rep')
@op_input('delimiter', nullable=True)
@op_input('quotechar', nullable=True)
@op_input('more_args', nullable=True, data_type=DictLike)
def write_csv(obj: DataFrameLike.TYPE,
              file: FileLike.TYPE,
              columns: VarNamesLike.TYPE = None,
              na_rep: str = '',
              delimiter: str = ',',
              quotechar: str = None,
              more_args: DictLike.TYPE = None,
              monitor: Monitor = Monitor.NONE):
    """
    Write comma-separated values (CSV) to plain text file from a DataFrame or Dataset.

    :param obj: The object to write as CSV; must be a ``DataFrame`` or a ``Dataset``.
    :param file: The CSV file path.
    :param columns: The names of variables that should be converted to columns. If given,
           coordinate variables are included automatically.
    :param delimiter: Delimiter to use.
    :param na_rep: A string representation of a missing value (no-data value).
    :param quotechar: The character used to denote the start and end of a quoted item.
           Quoted items can include the delimiter and it will be ignored.
    :param more_args: Other optional keyword arguments.
           Please refer to Pandas documentation of ``pandas.to_csv()`` function.
    :param monitor: optional progress monitor
    """
    if obj is None:
        raise ValidationError('obj must not be None')

    columns = VarNamesLike.convert(columns)

    if isinstance(obj, pd.DataFrame):
        # The following code is needed, because Pandas treats any kw given in kwargs as being set, even if just None.
        kwargs = DictLike.convert(more_args)
        if kwargs is None:
            kwargs = {}
        if columns:
            kwargs.update(columns=columns)
        if delimiter:
            kwargs.update(sep=delimiter)
        if na_rep:
            kwargs.update(na_rep=na_rep)
        if quotechar:
            kwargs.update(quotechar=quotechar)
        with monitor.starting('Writing to CSV', 1):
            obj.to_csv(file, index_label='index', **kwargs)
            monitor.progress(1)
    elif isinstance(obj, xr.Dataset):
        var_names = [var_name for var_name in obj.data_vars if columns is None or var_name in columns]
        dim_names = None
        data_vars = []
        for var_name in var_names:
            data_var = obj.data_vars[var_name]
            if dim_names is None:
                dim_names = data_var.dims
            elif dim_names != data_var.dims:
                raise ValidationError('Not all variables have the same dimensions. '
                                      'Please select variables so that their dimensions are equal.')
            data_vars.append(data_var)
        if dim_names is None:
            raise ValidationError('None of the selected variables has a dimension.')

        coord_vars = []
        for dim_name in dim_names:
            if dim_name in obj.coords:
                coord_var = obj.coords[dim_name]
            else:
                coord_var = None
                for data_var in obj.coords.values():
                    if len(data_var.dims) == 1 and data_var.dims[0] == dim_name:
                        coord_var = data_var
                        break
                if coord_var is None:
                    raise ValueError(f'No coordinate variable found for dimension "{dim_name}"')
            coord_vars.append(coord_var)
        coord_indexes = [range(len(coord_var)) for coord_var in coord_vars]
        num_coords = len(coord_vars)

        num_rows = 1
        for coord_var in coord_vars:
            num_rows *= len(coord_var)

        stream = open(file, 'w') if isinstance(file, str) else file
        try:
            # Write header row
            stream.write('index')
            for i in range(num_coords):
                stream.write(delimiter)
                stream.write(coord_vars[i].name)
            for data_var in data_vars:
                stream.write(delimiter)
                stream.write(data_var.name)
            stream.write('\n')

            with monitor.starting('Writing CSV', num_rows):
                row = 0
                for index in itertools.product(*coord_indexes):
                    # Write data row
                    stream.write(str(row))
                    for i in range(num_coords):
                        coord_value = coord_vars[i].values[index[i]]
                        stream.write(delimiter)
                        stream.write(str(coord_value))
                    for data_var in data_vars:
                        var_value = data_var.values[index]
                        stream.write(delimiter)
                        stream.write(str(var_value))
                    stream.write('\n')
                    monitor.progress(1)
                    row += 1
        finally:
            if isinstance(file, str):
                stream.close()

    elif obj is None:
        raise ValidationError('obj must not be None')
    else:
        raise ValidationError('obj must be a pandas.DataFrame or a xarray.Dataset')


GEO_DATA_FRAME_FILE_FILTERS = [
    dict(name='ESRI Shapefile', extensions=['shp']),
    dict(name='GeoJSON', extensions=['json', 'geojson']),
    dict(name='GPX', extensions=['gpx']),
    dict(name='GPKG', extensions=['gpkg']),
    _ALL_FILE_FILTER
]


# noinspection PyIncorrectDocstring,PyUnusedLocal
@op(tags=['input'], res_pattern='gdf_{index}')
@op_input('file', file_open_mode='r', file_filters=GEO_DATA_FRAME_FILE_FILTERS)
@op_input('crs', nullable=True, deprecated="Not used at all.")
@op_input('more_args', nullable=True, data_type=DictLike)
def read_geo_data_frame(file: str, crs: str = None,
                        more_args: DictLike.TYPE = None) -> gpd.GeoDataFrame:
    """
    Read a geo data frame from a file with a format such as ESRI Shapefile or GeoJSON.

    :param file: Is either the absolute or relative path to the file to be opened.
    :param more_args: Other optional keyword arguments.
           Please refer to Python documentation of ``fiona.open()`` function.
    :return: A ``geopandas.GeoDataFrame`` object
    """
    kwargs = DictLike.convert(more_args) or {}
    features = fiona.open(file, mode="r", **kwargs)
    return GeoDataFrame.from_features(features)


# noinspection PyIncorrectDocstring,PyUnusedLocal
@op(tags=['output'], no_cache=True)
@op_input('gdf')
@op_input('file', file_open_mode='w', file_filters=GEO_DATA_FRAME_FILE_FILTERS)
@op_input('more_args', nullable=True, data_type=DictLike)
def write_geo_data_frame(gdf: gpd.GeoDataFrame,
                         file: str, crs: str = None,
                         more_args: DictLike.TYPE = None):
    """
    Write a geo data frame to files with formats such as ESRI Shapefile or GeoJSON.

    :param gdf: A geo data frame.
    :param file: Is either the absolute or relative path to the file to be opened.
    :param more_args: Other optional keyword arguments.
           Please refer to Python documentation of ``fiona.open()`` function.
    """
    kwargs = DictLike.convert(more_args) or {}
    if "driver" in kwargs:
        driver = kwargs.pop("driver")
    else:
        root, ext = os.path.splitext(file)
        ext_low = ext.lower()
        if ext_low == "":
            driver = "ESRI Shapefile"
            file += ".shp"
        elif ext_low == ".shp":
            driver = "ESRI Shapefile"
        elif ext_low == ".json" or ext_low == ".geojson":
            driver = "GeoJSON"
        elif ext_low == ".gpx":
            driver = "GPX"
        elif ext_low == ".gpkg":
            driver = "GPKG"
        else:
            raise ValidationError(f'Cannot detect supported format from file extension "{ext}"')
    gdf.to_file(file, driver=driver, **kwargs)


@op(tags=['input'], res_pattern='ds_{index}')
@op_input('path',
          file_open_mode='r',
          file_filters=[dict(name='Zarr', extensions=['zarr'])],
          file_props=['openDirectory'])
@op_input('drop_variables', data_type=VarNamesLike)
def read_zarr(path: str,
              key: str = None,
              secret: str = None,
              token: str = None,
              drop_variables: VarNamesLike.TYPE = None,
              decode_cf: bool = True,
              decode_times: bool = True,
              normalize: bool = True) -> xr.Dataset:
    """
    Read a dataset from a Zarr directory, Zarr ZIP archive, or remote Zarr object storage.

    For the Zarr format, refer to http://zarr.readthedocs.io/en/stable/.

    :param path: Zarr directory path, Zarr ZIP archive path, or (S3) object storage URL.
    :param key: Optional (AWS) access key identifier. Valid only if *path* is a URL.
    :param secret: Optional (AWS) secret access key. Valid only if *path* is a URL.
    :param token: Optional (AWS) access token. Valid only if *path* is a URL.
    :param drop_variables: List of variables to be dropped.
    :param decode_cf: Whether to decode CF attributes and coordinate variables.
    :param decode_times: Whether to decode time information (convert time coordinates to ``datetime`` objects).
    :param normalize: Whether to normalize the dataset's geo- and time-coding upon opening. See operation ``normalize``.
    """
    drop_variables = VarNamesLike.convert(drop_variables)

    is_s3_url = path.startswith('s3://')
    is_http_url = path.startswith('http://') or path.startswith('https://')
    if is_s3_url or is_http_url:
        root = path
        client_kwargs = None
        if is_http_url:
            url = urllib.parse.urlparse(path)
            root = url.path[1:] if url.path.startswith('/') else url.path
            client_kwargs = dict(endpoint_url=f'{url.scheme}://{url.netloc}')
        store = s3fs.S3Map(root, s3=s3fs.S3FileSystem(anon=not (key or secret or token),
                                                      key=key,
                                                      secret=secret,
                                                      token=token,
                                                      client_kwargs=client_kwargs))
    else:
        store = path

    ds = xr.open_zarr(store,
                      drop_variables=drop_variables,
                      decode_cf=decode_cf,
                      decode_times=decode_times)
    if normalize:
        return adjust_temporal_attrs(normalize_op(ds))
    return ds


@op(tags=['output'], no_cache=True)
@op_input('path', file_open_mode='w', file_filters=[dict(name='Zarr', extensions=['zarr'])])
def write_zarr(ds: xr.Dataset, path: str) -> xr.Dataset:
    """
    Write dataset to a Zarr directory.

    For the Zarr format, refer to http://zarr.readthedocs.io/en/stable/.

    :param ds: An xarray dataset.
    :param path: Zarr directory path.
    """
    ds.to_zarr(path)
    return ds


@op(tags=['input'], res_pattern='ds_{index}')
@op_input('file', file_open_mode='r', file_filters=[dict(name='NetCDF', extensions=['nc'])])
@op_input('drop_variables', data_type=VarNamesLike)
@op_input('decode_cf')
@op_input('normalize')
@op_input('decode_times')
@op_input('engine')
def read_netcdf(file: str,
                drop_variables: VarNamesLike.TYPE = None,
                decode_cf: bool = True,
                normalize: bool = True,
                decode_times: bool = True,
                engine: str = None) -> xr.Dataset:
    """
    Read a dataset from a netCDF 3/4 or HDF file.

    :param file: The netCDF file path.
    :param drop_variables: List of variables to be dropped.
    :param decode_cf: Whether to decode CF attributes and coordinate variables.
    :param normalize: Whether to normalize the dataset's geo- and time-coding upon opening. See operation ``normalize``.
    :param decode_times: Whether to decode time information (convert time coordinates to ``datetime`` objects).
    :param engine: Optional netCDF engine name.
    """
    drop_variables = VarNamesLike.convert(drop_variables)
    ds = xr.open_dataset(file,
                         drop_variables=drop_variables,
                         decode_cf=decode_cf,
                         decode_times=decode_times,
                         engine=engine)
    chunks = get_spatial_ext_chunk_sizes(ds)
    if chunks and 'time' in ds.dims:
        chunks['time'] = 1
    if chunks:
        ds = ds.chunk(chunks)
    if normalize:
        return adjust_temporal_attrs(normalize_op(ds))
    return ds


@op(tags=['output'], no_cache=True)
@op_input('obj')
@op_input('file', file_open_mode='w', file_filters=[dict(name='NetCDF 3', extensions=['nc'])])
@op_input('engine')
def write_netcdf3(obj: xr.Dataset, file: str, engine: str = None):
    """
    Write a data object to a netCDF 3 file. Note that the data object must be netCDF-serializable.

    :param obj: A netCDF-serializable data object.
    :param file: The netCDF file path.
    :param engine: Optional netCDF engine to be used
    """
    obj.to_netcdf(file, format='NETCDF3_64BIT', engine=engine)


@op(tags=['output'], no_cache=True)
@op_input('obj')
@op_input('file', file_open_mode='w', file_filters=[dict(name='NetCDF 4', extensions=['nc'])])
@op_input('engine')
def write_netcdf4(obj: xr.Dataset, file: str, engine: str = None):
    """
    Write a data object to a netCDF 4 file. Note that the data object must be netCDF-serializable.

    :param obj: A netCDF-serializable data object.
    :param file: The netCDF file path.
    :param engine: Optional netCDF engine to be used
    """
    obj.to_netcdf(file, format='NETCDF4', engine=engine)


# noinspection PyAbstractClass
class TextObjectIO(ObjectIO):
    @property
    def description(self):
        return "Plain text format"

    @property
    def format_name(self):
        return 'TEXT'

    @property
    def filename_ext(self):
        return '.txt'

    @property
    def read_op(self):
        return OP_REGISTRY.get_op('read_text')

    @property
    def write_op(self):
        return OP_REGISTRY.get_op('write_text')

    def read_fitness(self, file):
        # Basically every object can be written to a text file: str(obj)
        return 1 if isinstance(file, str) and os.path.isfile(file) else 0

    def write_fitness(self, obj):
        return 1000 if isinstance(obj, str) else 1


# noinspection PyAbstractClass
class JsonObjectIO(ObjectIO):
    @property
    def description(self):
        return 'JSON format (plain text, UTF8)'

    @property
    def format_name(self):
        return 'JSON'

    @property
    def filename_ext(self):
        return '.json'

    @property
    def read_op(self):
        return OP_REGISTRY.get_op('read_json')

    @property
    def write_op(self):
        return OP_REGISTRY.get_op('write_json')

    def read_fitness(self, file):
        return 1 if isinstance(file, str) and os.path.isfile(file) else 0

    def write_fitness(self, obj):
        return 1000 if isinstance(obj, str) \
                       or isinstance(obj, float) \
                       or isinstance(obj, int) \
                       or isinstance(obj, list) \
                       or isinstance(obj, dict) else 0


class NetCDFObjectIO(ObjectIO, metaclass=ABCMeta):
    @property
    def filename_ext(self):
        return '.nc'

    def read_fitness(self, file):
        # noinspection PyBroadException
        try:
            dataset = self.read(file)
            dataset.close()
            return 100000
        except Exception:
            return -1

    def write_fitness(self, obj):
        # TODO (forman, 20160905): add support for numpy-like arrays
        return 100000 if isinstance(obj, xr.Dataset) or (hasattr(obj, 'to_netcdf') and callable(obj.to_netcdf)) \
            else 0


# noinspection PyAbstractClass
class NetCDF3ObjectIO(NetCDFObjectIO):
    @property
    def description(self):
        return "netCDF 3 file format, which fully supports 2+ GB files."

    @property
    def format_name(self):
        return 'NETCDF3'

    @property
    def read_op(self):
        return OP_REGISTRY.get_op('read_netcdf')

    @property
    def write_op(self):
        return OP_REGISTRY.get_op('write_netcdf3')


# noinspection PyAbstractClass
class NetCDF4ObjectIO(NetCDFObjectIO):
    @property
    def description(self):
        return "netCDF 4 file format (HDF5 file format, using netCDF 4 API features)"

    @property
    def format_name(self):
        return 'NETCDF4'

    @property
    def read_op(self):
        return OP_REGISTRY.get_op('read_netcdf')

    @property
    def write_op(self):
        return OP_REGISTRY.get_op('write_netcdf4')


OBJECT_IO_REGISTRY.object_io_list.extend([
    TextObjectIO(),
    JsonObjectIO(),
    NetCDF4ObjectIO(),
    NetCDF3ObjectIO()
])
