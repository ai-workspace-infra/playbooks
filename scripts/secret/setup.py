from setuptools import setup, find_packages

setup(
    name="hcp_secret",
    version="0.1",
    packages=find_packages(include=['secret', 'secret.hcp']),
    install_requires=[
        "requests",
    ],
    tests_require=[
        "unittest",
    ],
    description="A library to fetch secrets from HCP Cloud",
    author="Haitao Pan",
    author_email="manbuzhe2008@gmail.com",
    url="https://github.com/yourusername/hcp_secret",
)
