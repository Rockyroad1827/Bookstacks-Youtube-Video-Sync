#!/bin/bash

# =================================================================
# BookStack Recycle Bin Purge Script
# This script executes a database query to permanently remove items
# from the recycle_bin table that were deleted by a specific user ID.
# =================================================================




# --- Configuration ---
# WARNING: Replace all placeholder values below with your actual data.

# Database Credentials and Name
DB_USER=""      # Your database username
DB_PASS=""  # Your database password
DB_HOST=""           # Your database host (e.g., localhost, 127.0.0.1)
DB_NAME=""        # The name of your BookStack database

# The user ID whose recycled items will be purged.
# IMPORTANT: You must find the numeric user_id from your BookStack 'users' table.
USER_ID_TO_PURGE="" # Example ID: Replace '5' with the actual user ID.

# Path to the SQL file containing the purge query
SQL_FILE="./purge_query.sql"

#---------------------------------------------------------------#




# --- Pre-Execution Checks ---
if [ ! -f "$SQL_FILE" ]; then
    echo "Error: SQL file '$SQL_FILE' not found."
    exit 1
fi

echo "--- Starting Recycle Bin Purge ---"
echo "Target User ID: ${USER_ID_TO_PURGE}"
echo "Database: ${DB_NAME}"

# --- Prepare Query ---
# The sed command dynamically replaces the placeholders in the SQL file
# with the actual database name and user ID.
PURGE_QUERY=$(cat "$SQL_FILE" | \
  sed "s/DB_NAME_HERE/${DB_NAME}/g" | \
  sed "s/USER_ID_TO_PURGE/${USER_ID_TO_PURGE}/g" \
)

# --- Execute Query ---
# The mysql command executes the prepared query using the configured credentials.
mysql -h "${DB_HOST}" -u "${DB_USER}" -p"${DB_PASS}" -e "${PURGE_QUERY}" 2>&1

# Check the exit status of the mysql command
if [ $? -eq 0 ]; then
    echo "Successfully purged recycle bin items deleted by user ID ${USER_ID_TO_PURGE}."
else
    echo "ERROR: Database purge failed. Check connection details and permissions."
fi

echo "--- Script Finished ---"

exit 0