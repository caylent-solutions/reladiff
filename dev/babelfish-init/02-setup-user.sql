-- Set up Babelfish user with proper permissions for testing

-- Ensure the user has proper permissions
ALTER USER babelfish_user CREATEDB;
ALTER USER babelfish_user CREATEROLE;

-- Grant necessary schema permissions
GRANT USAGE ON SCHEMA public TO babelfish_user;
GRANT CREATE ON SCHEMA public TO babelfish_user;
GRANT USAGE ON SCHEMA dbo TO babelfish_user;
GRANT CREATE ON SCHEMA dbo TO babelfish_user;

-- Set search path to include both schemas
ALTER USER babelfish_user SET search_path = dbo, public;

-- Create additional schemas that might be needed for testing
CREATE SCHEMA IF NOT EXISTS testschema;
GRANT USAGE, CREATE ON SCHEMA testschema TO babelfish_user;