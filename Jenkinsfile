pipeline {
    agent any

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build') {
            steps {
                echo 'Application source code checked out successfully.'
                echo 'Python dependencies will be installed inside the Docker image.'
            }
        }

        stage('Docker Build') {
            steps {
                bat 'docker build -t space-app:3.0 .'
            }
        }

        stage('Security Scan') {
            steps {
                bat 'trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed space-app:3.0'
            }
        }
    }
}
