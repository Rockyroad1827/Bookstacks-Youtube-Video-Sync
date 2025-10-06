#!/bin/bash

# =================================================================
# BookStack & YouTube Sync Configuration Setup Script (Fully Automated)
#
# This script handles setup, cloning, VENV creation, configuration injection,
# and final cron job creation for automated execution.
# =================================================================

# --- Configuration & File Paths ---
# The fixed repository URL
GITHUB_REPO_URL="https://github.com/Rockyroad1827/Bookstacks-Youtube-Video-Sync.git"
# Target subdirectory within the BookStack base directory
SYNC_TARGET_DIR="Scripts/YoutubeSync" 
VENV_NAME=".venv"

# Initialize variables for paths
SYNC_SCRIPT_NAME="Sync Public Videos.py"
PURGE_SHELL_SCRIPT_NAME="purge_recycle_bin.sh"
PURGE_SQL_SCRIPT_NAME="purge_query.sql"
BOOKSTACK_BASE_DIR=""

# --- Input Validation Function ---
prompt_for_variable() {
    local prompt_text=$1
    local var_name=$2
    local current_value=""
    
    # Check if a default value is provided (optional 3rd argument)
    if [ ! -z "$3" ]; then
        current_value=" (default: $3)"
    fi

    while true; do
        read -r -p "$prompt_text$current_value: " input

        # If input is empty and a default is provided, use the default
        if [ -z "$input" ] && [ ! -z "$3" ]; then
            REPLY="$3"
            break
        fi
        
        # Simple check for empty input
        if [ -z "$input" ]; then
            echo "Error: This value cannot be empty. Please enter a value."
        else
            REPLY="$input"
            break
        fi
    done
}

# --- GitHub Clone Function ---
clone_github_repo() {
    local repo_url=$1
    local target_dir=$2

    echo -e "\n--- 2. Cloning GitHub Repository ---"
    
    # Check if git is installed
    if ! command -v git &> /dev/null; then
        echo "Error: 'git' command not found. Please install git (e.g., sudo apt install git) and re-run the script."
        exit 1
    fi
    
    # Check if target directory already contains files (skip cloning if it looks setup)
    if [ -d "$target_dir" ] && [ "$(ls -A "$target_dir" | wc -l)" -gt 2 ]; then
        echo "Directory $target_dir is not empty (it contains files). Skipping clone. Assuming files are already present."
        return 0
    fi
    
    # Create the target directory if it doesn't exist
    if [ ! -d "$target_dir" ]; then
        mkdir -p "$target_dir"
        echo "Created directory: $target_dir"
    fi

    # Clone the repository. Using --depth 1 for faster download.
    if git clone --depth 1 "$repo_url" temp_clone; then
        # Move contents from the temporary clone folder to the final target directory
        echo "Moving files into $target_dir..."
        mv temp_clone/* "$target_dir/" 
        mv temp_clone/.* "$target_dir/" 2>/dev/null 
        rm -rf temp_clone
        echo "Repository cloned successfully."
    else
        echo "ERROR: Failed to clone the repository from $repo_url. Check the URL and connectivity."
        rm -rf temp_clone 
        exit 1
    fi
}

# --- Virtual Environment Setup ---
setup_venv() {
    local target_dir=$1
    local venv_name=$2
    
    echo -e "\n--- 3. Setting up Python Virtual Environment (VENV) ---"
    
    # Install python3-venv package (Requires sudo)
    if ! dpkg -s python3-venv &> /dev/null; then
        echo "Installing python3-venv (requires sudo password)..."
        sudo apt update
        sudo apt install python3-venv -y
        if [ $? -ne 0 ]; then
            echo "ERROR: Failed to install python3-venv. Check apt repository status or run manually."
            exit 1
        fi
    else
        echo "python3-venv is already installed."
    fi
    
    # Create the virtual environment
    echo "Creating virtual environment at $target_dir/$venv_name ..."
    python3 -m venv "$target_dir/$venv_name"
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to create VENV. Check Python installation."
        exit 1
    fi
    
    # Install dependencies
    echo "Activating VENV and installing dependencies..."
    # Use the Python executable inside the VENV to ensure packages are installed there
    "$target_dir/$venv_name/bin/pip" install requests google-api-python-client google-auth-oauthlib uritemplate six httplib2 uritools urllib3 pandas numpy
    
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to install dependencies. Check network connection or dependency names."
        exit 1
    fi
    echo "VENV setup and dependencies installed successfully."
}

# --- Cron Job Setup ---
setup_cron() {
    local sync_path=$1
    local venv_path=$2
    local sync_script=$3
    
    echo -e "\n--- 8. Setting up CRON Job ---"
    
    # Prompt for frequency
    local freq_minutes
    prompt_for_variable "Enter the sync frequency in minutes (e.g., 60 for once per hour)" CRON_FREQ "60"
    freq_minutes=$REPLY

    if ! [[ "$freq_minutes" =~ ^[0-9]+$ ]] || [ "$freq_minutes" -lt 1 ]; then
        echo "Invalid frequency. Skipping cron job setup."
        return 1
    fi

    # The cron schedule pattern: runs every N minutes
    local cron_schedule="*/$freq_minutes * * * *"

    # The command to execute: (cd to script directory, source VENV, execute python script)
    local cron_command="cd ${sync_path} && source ${venv_path}/bin/activate && python \"${sync_script}\" >$BOOKSTACK_BASE_DIR/storage/logs/YoutubeSync.log 2>&1"
    local cron_job="${cron_schedule} ${cron_command}"

    # Add the cron job (using crontab -l | grep -v to prevent duplicates)
    (crontab -l 2>/dev/null | grep -v -F -- "${sync_script}") | crontab -
    (crontab -l 2>/dev/null; echo "${cron_job}") | crontab -

    if [ $? -eq 0 ]; then
        echo "Successfully added cron job:"
        echo "   Schedule: ${cron_schedule} (Every ${freq_minutes} minutes)"
        echo "   Command: ${cron_command}"
        echo "   Cron jobs installed by this user: $(crontab -l | grep -c ${sync_script})"
    else
        echo "ERROR: Failed to add cron job. Check user permissions for crontab."
    fi
}


# --- Start Execution ---
echo "--- Starting Configuration Setup (Fully Automated) ---"

# --- 1. Directory Confirmation ---
CURRENT_DIR=$(pwd)
echo -e "\n--- 1. BookStack Base Directory ---"
prompt_for_variable "Enter the BookStack base directory (e.g., /var/www/bookstack) or press Enter to use the current directory" BOOKSTACK_DIR_INPUT "${CURRENT_DIR}"

BOOKSTACK_BASE_DIR=$(echo "$REPLY" | sed 's/\/$//')

# Define final path
FINAL_SYNC_PATH="${BOOKSTACK_BASE_DIR}/${SYNC_TARGET_DIR}"
echo "BookStack Base Directory set to: ${BOOKSTACK_BASE_DIR}"

# --- 2. Clone GitHub Repository ---
clone_github_repo "$GITHUB_REPO_URL" "$FINAL_SYNC_PATH"

# Set script path variables now that the files exist in the target directory
SYNC_SCRIPT_FULLPATH="${FINAL_SYNC_PATH}/${SYNC_SCRIPT_NAME}"
PURGE_SHELL_SCRIPT_FULLPATH="${FINAL_SYNC_PATH}/${PURGE_SHELL_SCRIPT_NAME}"
PURGE_SQL_SCRIPT_FULLPATH="${FINAL_SYNC_PATH}/${PURGE_SQL_SCRIPT_NAME}"

# --- 3. VENV Setup ---
setup_venv "$FINAL_SYNC_PATH" "$VENV_NAME"

# --- 4. Update SQL Placeholders ---
echo -e "\n--- 4. Updating SQL Placeholders in $PURGE_SQL_SCRIPT_FULLPATH ---"
sed -i.bak \
    -e 's/Database_name\./DB_NAME_HERE\./g' \
    -e 's/UserID/USER_ID_TO_PURGE/g' \
    "${PURGE_SQL_SCRIPT_FULLPATH}"
rm "${PURGE_SQL_SCRIPT_FULLPATH}.bak"


# --- 5. Gather ALL Configuration Prompts ---
echo -e "\n--- 5. Gather Configuration Variables ---"
echo "BookStack API Settings:"
prompt_for_variable "Enter your BookStack Base URL (e.g., https://docs.example.com)" BOOKSTACK_URL
BOOKSTACK_URL_VAL=$REPLY

prompt_for_variable "Enter your BookStack API Token ID" BOOKSTACK_TOKEN_ID
BOOKSTACK_TOKEN_ID_VAL=$REPLY

prompt_for_variable "Enter your BookStack API Token Secret" BOOKSTACK_TOKEN_SECRET
BOOKSTACK_TOKEN_SECRET_VAL=$REPLY

prompt_for_variable "Enter the BookStack Book ID where content will be synced (Must be numeric)" TARGET_BOOK_ID
TARGET_BOOK_ID_VAL=$REPLY

echo -e "\nYouTube Configuration:"
prompt_for_variable "Enter your YouTube Channel ID (e.g., UC_...)" YOUTUBE_CHANNEL_ID
YOUTUBE_CHANNEL_ID_VAL=$REPLY

echo -e "\nDatabase Purge Configuration:"
prompt_for_variable "Enter your Database Host (e.g., 'localhost' or '127.0.0.1')" DB_HOST
DB_HOST_VAL=$REPLY

prompt_for_variable "Enter your Database Name (e.g., 'bookstack')" DB_NAME
DB_NAME_VAL=$REPLY

prompt_for_variable "Enter your Database User (with DELETE permissions)" DB_USER
DB_USER_VAL=$REPLY

prompt_for_variable "Enter your Database Password" DB_PASS
DB_PASS_VAL=$REPLY

prompt_for_variable "Enter the BookStack User ID whose deleted items will be PURGED (Must be numeric)" USER_ID_TO_PURGE
USER_ID_TO_PURGE_VAL=$REPLY


# --- 6. Apply Changes to Python Script (Sync Public Videos.py) ---
echo -e "\n--- 6. Applying configuration to $SYNC_SCRIPT_FULLPATH ---"
sed -i.bak \
    -e "s#^BOOKSTACK_URL = \".*\"#BOOKSTACK_URL = \"${BOOKSTACK_URL_VAL}\"#" \
    -e "s/^BOOKSTACK_TOKEN_ID = \".*\"/BOOKSTACK_TOKEN_ID = \"${BOOKSTACK_TOKEN_ID_VAL}\"/" \
    -e "s/^BOOKSTACK_TOKEN_SECRET = \".*\"/BOOKSTACK_TOKEN_SECRET = \"${BOOKSTACK_TOKEN_SECRET_VAL}\"/" \
    -e "s/^TARGET_BOOK_ID = [0-9]*/TARGET_BOOK_ID = ${TARGET_BOOK_ID_VAL}/" \
    -e "s/^YOUTUBE_CHANNEL_ID = \".*\"/YOUTUBE_CHANNEL_ID = \"${YOUTUBE_CHANNEL_ID_VAL}\"/" \
    "${SYNC_SCRIPT_FULLPATH}"
rm "${SYNC_SCRIPT_FULLPATH}.bak"


# --- 7. Apply Changes to Shell Script (purge_recycle_bin.sh) ---
echo -e "\n--- 7. Applying configuration to $PURGE_SHELL_SCRIPT_FULLPATH ---"
sed -i.bak \
    -e "s/^DB_USER=\".*\"/DB_USER=\"${DB_USER_VAL}\"/" \
    -e "s/^DB_PASS=\".*\"/DB_PASS=\"${DB_PASS_VAL}\"/" \
    -e "s/^DB_HOST=\".*\"/DB_HOST=\"${DB_HOST_VAL}\"/" \
    -e "s/^DB_NAME=\".*\"/DB_NAME=\"${DB_NAME_VAL}\"/" \
    -e "s/USER_ID_TO_PURGE=\".*\"/USER_ID_TO_PURGE=\"${USER_ID_TO_PURGE_VAL}\"/" \
    "${PURGE_SHELL_SCRIPT_FULLPATH}"
rm "${PURGE_SHELL_SCRIPT_FULLPATH}.bak"


# --- 8. Setup CRON Job ---
setup_cron "$FINAL_SYNC_PATH" "$VENV_NAME" "$SYNC_SCRIPT_NAME"


echo -e "\n--- Configuration Setup Complete 🚀 ---"
echo "All configurations have been applied. Your environment and scripts are installed in:"
echo "   ${FINAL_SYNC_PATH}"
echo ""
echo "NEXT STEPS:"
echo "1. Run the Purge Test (optional, but recommended):"
echo "   cd ${FINAL_SYNC_PATH}"
echo "   bash purge_recycle_bin.sh"
echo "2. Run the Sync Script MANUALLY ONCE for Google OAuth:"
echo "   cd ${FINAL_SYNC_PATH}"
echo "   source ${VENV_NAME}/bin/activate"
echo "   python3 \"${SYNC_SCRIPT_NAME}\""
echo "   (This step will open a browser for Google login. After this, the cron job will run automatically.)"
