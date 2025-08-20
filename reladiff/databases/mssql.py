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
    """Provides MD5 hashing functionality for SQL Server"""
    
    def md5_as_int(self, s: str) -> str:
        """Convert MD5 hash to integer representation for SQL Server"""
        return f"CONVERT(BIGINT, CONVERT(BINARY(8), HASHBYTES('MD5', {s}), 1))"

    def md5_as_hex(self, s: str) -> str:
        """Convert MD5 hash to hexadecimal string for SQL Server"""
        return f"CONVERT(VARCHAR(32), HASHBYTES('MD5', {s}), 2)"


class Mixin_NormalizeValue:
    """Provides value normalization for SQL Server"""
    
    def normalize_number(self, value, coltype) -> str:
        """Normalize numeric values for SQL Server"""
        return str(value)
    
    def normalize_timestamp(self, value, coltype) -> str:
        """Normalize timestamp values for SQL Server"""
        if hasattr(value, 'isoformat'):
            return f"'{value.isoformat()}'"
        return f"'{value}'"
    
    def normalize_boolean(self, value, coltype) -> str:
        """Normalize boolean values for SQL Server"""
        return '1' if value else '0'


class Dialect(BaseDialect, Mixin_MD5, Mixin_NormalizeValue, ReladiffDialect):
    """SQL Server dialect implementation"""
    
    name = 'MSSQL'
    ROUNDS_ON_PREC_LOSS = True
    
    TYPE_CLASSES = {
        # Numeric types
        'int': Integer,
        'bigint': Integer,
        'smallint': Integer,
        'tinyint': Integer,
        'decimal': Decimal,
        'numeric': Decimal,
        'float': Float,
        'real': Float,
        'money': Decimal,
        'smallmoney': Decimal,
        # String types
        'varchar': String_VaryingAlphanum,
        'nvarchar': String_VaryingAlphanum,
        'char': String_Alphanum,
        'nchar': String_Alphanum,
        'text': Text,
        'ntext': Text,
        # Date/time types
        'datetime': Datetime,
        'datetime2': Datetime,
        'smalldatetime': Datetime,
        'date': Date,
        'time': Timestamp,
        'datetimeoffset': Timestamp,
        # Binary types
        'binary': String_VaryingAlphanum,
        'varbinary': String_VaryingAlphanum,
        'image': Text,
        # Other types
        'bit': Boolean,
        'uniqueidentifier': String_Alphanum,
        'xml': Text,
    }
    
    def quote(self, s: str) -> str:
        """Quote identifier for SQL Server"""
        return f'[{s}]'
    
    def to_string(self, s: str) -> str:
        """Convert value to string representation for SQL Server"""
        return f"CAST({s} AS VARCHAR(MAX))"
    
    def set_timezone_to_utc(self) -> str:
        """Set session timezone to UTC for SQL Server"""
        # SQL Server doesn't have a session timezone setting like PostgreSQL
        # Return empty string to indicate no action needed
        raise NotImplementedError("SQL Server doesn't support session timezone setting")
    
    def current_timestamp(self) -> str:
        """Get current timestamp for SQL Server"""
        return "GETUTCDATE()"
    
    def offset_limit(self, offset: int = None, limit: int = None) -> str:
        """Generate OFFSET/LIMIT clause for SQL Server"""
        if limit is None and offset is None:
            return ""
        if offset is None:
            offset = 0
        
        result = f" OFFSET {offset} ROWS"
        if limit is not None:
            result += f" FETCH NEXT {limit} ROWS ONLY"
        return result


class MsSQL(ThreadedDatabase):
    """SQL Server database implementation using pymssql (FreeTDS)"""
    
    dialect = Dialect()
    SUPPORTS_PRIMARY_KEY = True
    SUPPORTS_INDEXES = True
    CONNECT_URI_PARAMS = ['database?']
    CONNECT_URI_HELP = "mssql://<user>:<password>@<host>:<port>/<database>"
    
    def __init__(self, *, host='localhost', port=1433, user='sa', password=None, 
                 database='master', thread_count=1, **kwargs):
        self.host = host
        self.port = port  
        self.user = user
        self.password = password
        self.database = database
        super().__init__(thread_count=thread_count, **kwargs)
    
    def create_connection(self):
        """Create connection to SQL Server using pymssql"""
        try:
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
        """Get table schema query for SQL Server"""
        if isinstance(path, (list, tuple)) and len(path) >= 2:
            schema, table = path[0], path[1]
        elif isinstance(path, str):
            if '.' in path:
                schema, table = path.split('.', 1)
            else:
                schema, table = 'dbo', path
        else:
            schema, table = 'dbo', str(path)
            
        # Return only the expected columns for sqeleton's parse_type method
        # The expected format is: col_name, type_repr, datetime_precision, numeric_precision, numeric_scale
        return f"""
            SELECT 
                COLUMN_NAME,
                DATA_TYPE,
                CASE 
                    WHEN DATA_TYPE LIKE '%time%' OR DATA_TYPE LIKE '%date%' THEN 3
                    ELSE NULL 
                END as datetime_precision,
                NUMERIC_PRECISION,
                NUMERIC_SCALE
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}'
            ORDER BY ORDINAL_POSITION
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