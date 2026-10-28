# Syntel - Django Project

A comprehensive Django-based management system for purchase orders, stock management, and analytics.

## Project Structure

```
syntel/
├── docs/                  # All project documentation
├── frontend/              # Frontend static files
├── logs/                  # Application logs
├── purchases/             # Main Django app
│   ├── management/        # Custom management commands
│   ├── migrations/        # Database migrations
│   ├── templates/         # HTML templates
│   ├── templatetags/      # Custom template tags
│   └── tests/             # App tests
├── scripts/               # Utility scripts
│   ├── deployment/        # Deployment scripts
│   ├── fixes/             # Data fix scripts
│   ├── management_commands/ # Admin utilities
│   ├── setup/             # Initial setup scripts
│   └── tests/             # Test utilities
├── staticfiles/           # Collected static files
├── syntel/                # Django project settings
│   ├── settings.py        # Development settings
│   └── settings_production.py # Production settings
├── temp/                  # Temporary files
├── tests/                 # Project-wide tests
├── manage.py              # Django management script
└── requirements.txt       # Python dependencies
```

## Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL (for production) or SQLite (for development)
- pip and virtualenv

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd syntel
```

2. Create and activate virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

5. Run migrations:
```bash
cd syntel
python manage.py migrate
```

6. Create superuser:
```bash
python manage.py createsuperuser
```

7. Run development server:
```bash
python manage.py runserver
```

Visit http://localhost:8000 to access the application.

## Configuration

### Environment Variables
Copy `.env.example` to `.env` and configure:
- `SECRET_KEY` - Django secret key
- `DEBUG` - Debug mode (True/False)
- `DATABASE_URL` - Database connection string
- Other app-specific settings

### Production Deployment
See [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) for detailed deployment instructions.

## Documentation

All documentation is located in the `docs/` directory:
- [User Manual](docs/USER_MANUAL.md)
- [Manager Manual](docs/MANAGER_MANUAL.md)
- [Technical Documentation](docs/TECHNICAL_DOCUMENTATION.md)
- [Deployment Guide](docs/DEPLOYMENT_GUIDE.md)
- [Troubleshooting Guide](docs/TROUBLESHOOTING_GUIDE.md)

## Scripts

Utility scripts are organized in the `scripts/` directory:

### Setup Scripts (`scripts/setup/`)
Initial data population and setup scripts.

### Management Commands (`scripts/management_commands/`)
Administrative utilities for data management and maintenance.

### Fix Scripts (`scripts/fixes/`)
Scripts to fix data inconsistencies or migration issues.

### Deployment Scripts (`scripts/deployment/`)
Deployment and validation scripts for production.

## Testing

Run all tests:
```bash
python scripts/tests/runtests.py
```

Run specific test:
```bash
python manage.py test purchases.tests.test_models
```

## Features

- Purchase order management
- Stock tracking and inventory management
- Financial analytics and reporting
- Multi-cafe support
- Role-based access control
- Real-time dashboards
- Mobile-responsive interface

## Technology Stack

- **Backend**: Django 4.x
- **Database**: PostgreSQL / SQLite
- **Frontend**: HTML, CSS, JavaScript
- **Deployment**: Docker, Nginx
- **API**: Django REST Framework

## Contributing

1. Create a feature branch
2. Make your changes
3. Run tests
4. Submit a pull request

## License

[Your License Here]

## Support

For issues and questions, please refer to:
- [Troubleshooting Guide](docs/TROUBLESHOOTING_GUIDE.md)
- [Technical Documentation](docs/TECHNICAL_DOCUMENTATION.md)
