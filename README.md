GDC & SYJ Performance Testing Suite

Project: Gestor de Comprobantes (GDC) / Sueldos y Jornales (SYJ)


1. Overview

This repository contains the load testing suite for the Gestor de Comprobantes (GDC) and Sueldos y Jornales (SYJ) modules. The scripts are built using Locust to simulate real-world user behavior and validate the system's stability under high concurrency, specifically focusing on Azure-hosted SaaS integrations.

2. Prerequisites

    Python: 3.9 or higher

    OS: Linux/Ubuntu (Recommended for high-load simulation)

    Tools: Access to Azure Monitor for server-side metrics correlation.

3. Installation & Setup

Follow these steps to initialize the testing environment:
Bash

    # Clone the repository and navigate to the folder
    cd locus-holistor

    # Create a virtual environment
    python3 -m venv venv

    # Activate the environment
    source venv/bin/activate

    # Install required dependencies
    pip install -r requirements.txt

4. Configuration

The suite uses a .env file for environment-specific configurations.
Variable  Description Options
TARGET_ENV  Sets the SYJ destination domain QA, STG, UAT, PROD

    Test Data: Ensure the accounts.csv file is populated with valid credentials in the following format:
    tenancy,username,password

5. Execution Guide
A. Development & Debugging (Web UI)

Use this mode to monitor real-time charts and verify script logic.
Bash

    TARGET_ENV=STG locust -f main.py

    Open your browser at: http://localhost:8089

B. Formal Stress Testing (Headless Mode)

Used for official test runs and reporting. This configuration simulates 1,000 users as per the Test Plan.
Bash

    # Increase system file descriptor limits to prevent socket errors
    ulimit -n 65536

    # Run headless test for 1 hour with a report output
    locust -f main.py --headless -u 1000 -r 20 --run-time 1h --html reports/stress_test_report.html

6. Testing Scenarios

    S1 - Libro Sueldo Digital: Simulates heavy data exports.

    S2 - PDF Receipts (ZIP): Tests server-side compression and bulk file generation.

    S3 - PDF Receipts (View): Simulates individual file streaming and rendering.

    S4 - Annual Tax Recalculation: Validates performance on CPU-intensive tax logic.

7. Troubleshooting

    401 Unauthorized: Verify the Abp.TenantId and credentials in accounts.csv.

    Socket Errors: Ensure ulimit -n 65536 was executed before running the test.

    Timeout Errors: Check Azure App Service logs for potential thread pool exhaustion.

