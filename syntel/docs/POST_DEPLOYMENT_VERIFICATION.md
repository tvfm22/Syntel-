# Post-Deployment Verification Checklist

**Date:** December 9, 2025
**Target Environment:** Production

## 1. System Health Checks
- [ ] **Web Server**: Nginx is running and serving requests (`systemctl status nginx` or `docker ps`)
- [ ] **Application Server**: Gunicorn is active (`docker-compose logs web`)
- [ ] **Database**: PostgreSQL is accepting connections (`docker-compose exec db pg_isready`)
- [ ] **Cache**: Redis is responding to PING (`docker-compose exec redis redis-cli ping`)

## 2. Application Functionality
- [ ] **Login**: Admin and User login works
- [ ] **Static Files**: CSS/JS files load correctly (no 404s)
- [ ] **Media Files**: Uploaded files (invoices) can be retrieved
- [ ] **Dashboard**: Main dashboard loads within 2 seconds
- [ ] **Reports**: PDF/Excel export functionality works

## 3. Security Verification
- [ ] **SSL**: Certificate is valid and HTTPS is enforced
- [ ] **Headers**: Security headers (HSTS, X-Frame-Options) are present
- [ ] **Debug**: Django Debug page is NOT visible on errors (404/500)
- [ ] **Admin**: Admin URL is accessible only to authorized IPs (if configured)

## 4. Monitoring & Logging
- [ ] **Logs**: Application logs are being written to `/var/log/` or Docker logs
- [ ] **Errors**: Sentry (if configured) is receiving test events
- [ ] **Metrics**: System resources (CPU/RAM) are within normal limits

## 5. Backup Verification
- [ ] **Database**: Manual backup can be created (`pg_dump`)
- [ ] **Restore**: Test restore procedure on a staging environment

## Sign-off
**Verified By:** ____________________
**Date:** ____________________
