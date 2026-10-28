# Scripts Directory

This directory contains various utility scripts organized by category.

## Directory Structure

### `/setup`
Scripts for initial setup and data population:
- `create_*.py` - Scripts to create sample/initial data
- `setup_*.py` - Setup scripts for various components

### `/fixes`
Scripts to fix data or configuration issues:
- `fix_*.py` - Various fix scripts

### `/management_commands`
Administrative and management scripts:
- `check_*.py` - Data validation and checking scripts
- `display_*.py` - Display information scripts
- `clear_cache.*` - Cache management
- `verify_*.py` - Verification scripts
- Other management utilities

### `/tests`
Test files and test utilities:
- `test_*.py` - Test scripts
- `runtests.py` - Test runner
- Test HTML files

### `/deployment`
Deployment and validation scripts:
- `deploy*.sh` - Deployment scripts
- `validate*.sh` - Validation scripts

## Usage

Most scripts should be run from the project root directory:

```bash
cd /path/to/Syntel
python syntel/scripts/setup/create_users.py
```

For bash scripts:
```bash
bash syntel/scripts/deployment/deploy.sh
```
