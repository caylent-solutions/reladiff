import pymssql
from sqeleton.databases.base import (
    Database, BaseDialect, ThreadedDatabase, ThreadLocalInterpreter,
    import_helper, ConnectError, QueryError
)
from sqeleton.abcs.mixins import AbstractMixin_MD5, AbstractMixin_NormalizeValue
from sqeleton.abcs.database_types import (
    Integer, Float, Decimal, String_VaryingAlphanum, String_Alphanum, 
    Boolean, Datetime, Date, Timestamp, Text
)
from .base import ReladiffDialect


class Mixin_MD5:
    """Provides MD5 hashing functionality for Babelfish"""
    
    def md5_as_int(self, s: str) -> str:
        """Convert MD5 hash to integer representation for Babelfish"""
        # Use PostgreSQL's md5 function since Babelfish is built on PostgreSQL
        return f"('x' || substring(md5({s}), 1, 16))::bit(64)::bigint"

    def md5_as_hex(self, s: str) -> str:
        """Convert MD5 hash to hexadecimal string for Babelfish"""
        return f"md5({s})"


class Mixin_NormalizeValue:
    """Provides value normalization for Babelfish"""
    
    def normalize_number(self, value, coltype) -> str:
        """Normalize numeric values for Babelfish"""
        return str(value)
    
    def normalize_timestamp(self, value, coltype) -> str:
        """Normalize timestamp values for Babelfish"""
        if hasattr(value, 'isoformat'):
            return f"'{value.isoformat()}'"
        return f"'{value}'"
    
    def normalize_boolean(self, value, coltype) -> str:
        """Normalize boolean values for Babelfish"""
        return 'true' if value else 'false'


class Dialect(BaseDialect, Mixin_MD5, Mixin_NormalizeValue, ReladiffDialect):
    """Babelfish dialect implementation - SQL Server compatibility on PostgreSQL"""
    
    name = 'Babelfish'
    ROUNDS_ON_PREC_LOSS = True
    
    TYPE_CLASSES = {
        # Numeric types (SQL Server compatible)
        'int': Integer,
        'integer': Integer,  # PostgreSQL native type
        'bigint': Integer,
        'smallint': Integer,
        'tinyint': Integer,
        'decimal': Decimal,
        'numeric': Decimal,
        'float': Float,
        'real': Float,
        'money': Decimal,
        'smallmoney': Decimal,
        'double precision': Float,
        # String types (SQL Server compatible)
        'varchar': String_VaryingAlphanum,
        'nvarchar': String_VaryingAlphanum,
        'char': String_Alphanum,
        'nchar': String_Alphanum,
        'text': Text,
        'ntext': Text,
        # PostgreSQL native types also supported
        'character varying': String_VaryingAlphanum,
        'character': String_Alphanum,
        # Date/time types
        'datetime': Datetime,
        'datetime2': Datetime,
        'smalldatetime': Datetime,
        'date': Date,
        'time': Timestamp,
        'datetimeoffset': Timestamp,
        'timestamp': Timestamp,
        'timestamptz': Timestamp,
        'timestamp without time zone': Timestamp,
        'timestamp with time zone': Timestamp,
        # Binary types
        'binary': String_VaryingAlphanum,
        'varbinary': String_VaryingAlphanum,
        'image': Text,
        'bytea': String_VaryingAlphanum,
        # Other types
        'bit': Boolean,
        'boolean': Boolean,
        'uniqueidentifier': String_Alphanum,
        'uuid': String_Alphanum,
        'xml': Text,
    }
    
    def quote(self, s: str) -> str:
        """Quote identifier for Babelfish (uses SQL Server bracket notation)"""
        # Use SQL Server style brackets since Babelfish supports T-SQL syntax
        return f'[{s}]'
    
    def to_string(self, s: str) -> str:
        """Convert value to string representation for Babelfish"""
        # Use CAST which works in both SQL Server and PostgreSQL contexts
        return f"CAST({s} AS VARCHAR)"
    
    def set_timezone_to_utc(self) -> str:
        """Set session timezone to UTC for Babelfish"""
        # Use T-SQL syntax since Babelfish communicates via TDS protocol
        return "SET DATEFORMAT ymd"
    
    def current_timestamp(self) -> str:
        """Get current timestamp for Babelfish"""
        # Use GETUTCDATE() for SQL Server compatibility
        return "GETUTCDATE()"
    
    def offset_limit(self, offset: int = None, limit: int = None) -> str:
        """Generate OFFSET/LIMIT clause for Babelfish"""
        if limit is None and offset is None:
            return ""
        if offset is None:
            offset = 0
        
        # Use SQL Server 2012+ OFFSET/FETCH syntax for compatibility
        result = f" OFFSET {offset} ROWS"
        if limit is not None:
            result += f" FETCH NEXT {limit} ROWS ONLY"
        return result


class Babelfish(ThreadedDatabase):
    """Babelfish database implementation - connects via TDS protocol with T-SQL support"""
    
    dialect = Dialect()
    SUPPORTS_PRIMARY_KEY = True
    SUPPORTS_INDEXES = True
    CONNECT_URI_PARAMS = ['database?']
    CONNECT_URI_HELP = "babelfish://<user>:<password>@<host>:<port>/<database>"
    
    def __init__(self, *, host='localhost', port=1434, user='sa', password=None, 
                 database='master', thread_count=1, **kwargs):
        self.host = host
        self.port = port  
        self.user = user
        self.password = password
        self.database = database
        super().__init__(thread_count=thread_count, **kwargs)
    
    def create_connection(self):
        """Create connection to Babelfish using TDS protocol via pymssql"""
        try:
            # Connect to Babelfish using TDS protocol - same as MSSQL
            return pymssql.connect(
                server=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                port=self.port,
                timeout=30,
                login_timeout=30,
                charset='UTF-8'
            )
        except pymssql.Error as e:
            raise ConnectError(*e.args) from e
    
    def select_table_schema(self, path) -> str:
        """Get table schema query for Babelfish using T-SQL"""
        if isinstance(path, (list, tuple)) and len(path) >= 2:
            schema, table = path[0], path[1]
        elif isinstance(path, str):
            if '.' in path:
                schema, table = path.split('.', 1)
            else:
                schema, table = 'dbo', path
        else:
            schema, table = 'dbo', str(path)
            
        # Use T-SQL system views available in Babelfish
        return f"""
            SELECT 
                c.column_name,
                c.data_type,
                CASE 
                    WHEN c.data_type LIKE '%time%' OR c.data_type LIKE '%date%' THEN 3
                    ELSE NULL 
                END as datetime_precision,
                c.numeric_precision,
                c.numeric_scale
            FROM information_schema.columns c
            WHERE c.table_schema = '{schema}' AND c.table_name = '{table}'
            ORDER BY c.ordinal_position
        """
    
    def _parse_table_name(self, path) -> tuple:
        """Parse table name into schema and table parts"""
        if isinstance(path, (list, tuple)):
            path = '.'.join(path)
        if '.' in path:
            schema, table = path.split('.', 1)
        else:
            schema, table = 'dbo', path
        return schema, table
    
    def parse_table_name(self, name: str):
        """Parse table name for database"""
        if '.' in name:
            return tuple(name.split('.'))
        return ('dbo', name)