// pipeline-library/vars/ansibleSteps.groovy

// 检出代码
def checkoutCode() {
    stage('Checkout repository and submodules') {
        agent {
            docker { image 'your-docker-image' } // 替换为您的 Docker 镜像
        }
        steps {
            checkout scm
        }
    }
}

// 预先设置
def preSetup(String sshPassword) {
    stage('Pre Setup') {
        agent {
            docker { image 'your-docker-image' } // 替换为您的 Docker 镜像
        }
        steps {
            script {
                sh "echo \"${sshPassword}\" > ~/.vault_pass.txt"
                sh "echo 'ansible_password: \'xxxx\'' >> inventory/group_vars/all.yml"
                sh "echo 'ansible_become_password: \'xxxx\'' >> inventory/group_vars/all.yml"
            }
        }
    }
}

// 部署
def deploy(String sshUser, String instanceName, String installVersion) {
    stage('Deploy Ignition Server') {
        agent {
            docker { image 'your-docker-image' } // 替换为您的 Docker 镜像
        }
        steps {
            script {
                sh "export ANSIBLE_HOST_KEY_CHECKING=False"
                sh "ansible-playbook -u ${sshUser} -i inventor.ini -kK playbooks/server.yml -l ${instanceName} -e 'ign_install_ver=${installVersion}' --vault-password-file .vault_pass.txt --diff"
            }
        }
    }
}

// 后续设置
def postSetup() {
    stage('Post Setup') {
        agent {
            docker { image 'your-docker-image' } // 替换为您的 Docker 镜像
        }
        steps {
            script {
                sh "export ANSIBLE_HOST_KEY_CHECKING=False"
            }
        }
    }
}

// 检查
def check() {
    stage('Check') {
        agent {
            docker { image 'your-docker-image' } // 替换为您的 Docker 镜像
        }
        steps {
            script {
                // Add your check logic here
            }
        }
    }
}

return this // 返回以便导出所有函数
