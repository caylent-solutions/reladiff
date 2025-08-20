# ARM64 Setup Guide

This guide covers setup instructions for ARM64 platforms (Apple Silicon M1/M2/M3, ARM64 Linux).

## Key Changes for ARM64 Compatibility

### MSSQL Database Driver
**Issue**: Microsoft ODBC drivers are not readily available for ARM64 architectures.

**Solution**: We use `pymssql` (FreeTDS) instead of `pyodbc` for MSSQL connections.

#### Installation
```bash
# pymssql is automatically installed with poetry
poetry install

# Or manually with pip
pip install pymssql
```

#### Connection String Format
```python
# MSSQL connection string (same format, different driver underneath)
mssql_uri = "mssql://sa:Password123!@localhost:1433/master"
```

### Docker Considerations

#### MSSQL Container
The standard MSSQL container runs via emulation on ARM64:
```yaml
# docker-compose.yml
mssql:
  image: mcr.microsoft.com/mssql/server:2022-latest
  # Runs via emulation on ARM64 - works but may be slower
```

**For better performance on ARM64**, consider Azure SQL Edge:
```yaml
mssql:
  image: mcr.microsoft.com/azure-sql-edge:latest
  # Native ARM64 support, better performance
```

#### Docker Desktop Settings
For improved performance on macOS with Apple Silicon:
1. Open Docker Desktop
2. Go to Settings → Features in development  
3. Enable "Use Rosetta for x86/amd64 emulation on Apple Silicon"

## Testing Setup

### Quick Start (ARM64)
```bash
# 1. Install dependencies
poetry install

# 2. Start test databases
docker-compose up -d mysql postgres mssql

# 3. Run tests
poetry run unittest-parallel -j 4

# 4. Test MSSQL specifically
python -c "from reladiff import connect; conn = connect('mssql://sa:Password123!@localhost:1433/master'); print('MSSQL OK')"
```

### Cross-Database Testing
All combinations work on ARM64:
- MySQL ↔ PostgreSQL ✅
- MySQL ↔ MSSQL ✅  
- PostgreSQL ↔ MSSQL ✅
- DuckDB ↔ All databases ✅

### Performance Notes
- **DuckDB**: Native performance (in-memory)
- **MySQL/PostgreSQL**: Native ARM64 containers, full performance
- **MSSQL**: Runs via emulation, ~10-20% performance impact acceptable for testing

## Troubleshooting

### Common Issues

#### "Can't open lib 'ODBC Driver 17 for SQL Server'"
**Cause**: Trying to use pyodbc instead of pymssql
**Solution**: Ensure you're using the updated codebase with pymssql dependency

#### MSSQL Container Won't Start
**Cause**: Insufficient memory allocated to Docker
**Solution**: Increase Docker memory limit to at least 4GB

#### "Login failed for user 'sa'"
**Cause**: Container still initializing or password mismatch
**Solution**: Wait 30-60s after container start, verify password in docker-compose.yml

### Verification Commands
```bash
# Check architecture
uname -m  # Should show "arm64" on Apple Silicon

# Verify pymssql installation
python -c "import pymssql; print('pymssql:', pymssql.__version__)"

# Test database connections
python -c "
from reladiff import connect
print('MySQL:', 'OK' if connect('mysql://mysql:Password1@localhost/mysql') else 'FAIL')
print('PostgreSQL:', 'OK' if connect('postgresql://postgres:Password1@localhost/postgres') else 'FAIL')  
print('MSSQL:', 'OK' if connect('mssql://sa:Password123!@localhost:1433/master') else 'FAIL')
"
```

## Development Notes

### Dependency Management
- `pymssql` is now the standard MSSQL driver (replaces pyodbc)
- No additional system packages required
- Works in containers and virtual environments

### CI/CD Considerations
- ARM64 runners can use the same setup as x86_64
- Docker Buildx supports multi-architecture builds
- All tests pass on both architectures

### Performance Testing
Run benchmarks on both architectures:
```bash
# Standard benchmark
dev/benchmark.sh

# Architecture-specific results
poetry run python3 dev/graph.py --title "ARM64 Performance"
```

## Team Recommendations

1. **New team members on Apple Silicon**: Follow this guide for first-time setup
2. **Existing setups**: Run `poetry install` to update dependencies  
3. **CI/CD pipelines**: No changes needed, pymssql works on all platforms
4. **Docker environments**: Consider Azure SQL Edge for ARM64-specific deployments

The ARM64 implementation maintains full compatibility with existing x86_64 setups while providing native performance where possible.