-- Run as the PostgreSQL superuser. Change the password, and use the same value in .env (DB_PASSWORD).
CREATE USER library WITH PASSWORD 'library_dev_password' CREATEDB;
CREATE DATABASE library OWNER library;
