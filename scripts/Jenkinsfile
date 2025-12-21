pipeline {
    agent any
    stages {
        stage('Build') {
            steps {
                sh 'ansible-lint'
            }
        }
        stage('Pre Setup') {
            steps {
                sh "echo \"${secrets.ANSIBLE_SSH_PASSWORD}\" > ~/.vault_pass.txt"
                sh "echo 'ansible_password: \'xxxx\'' >> inventory/group_vars/all.yml"
                sh "echo 'ansible_become_password: \'xxxx\'' >> inventory/group_vars/all.yml"
            }
        }
        stage('Deploy') {
            steps {
                sh "ansible-playbook -u ${secrets.ANSIBLE_SSH_USER} -i inventor.ini -kK playbooks/server.yml -l ${params.instance_name} -e 'ign_install_ver=${params.install_version}' --vault-password-file .vault_pass.txt --diff"
            }
        }
        stage('Postsetup') {
            steps {
                echo "Todo"
            }
        }
    }
}
