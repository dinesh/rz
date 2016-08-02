try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

import sys, re

reqs = ['docker-compose', 'pykube', 'google-api-python-client', 'click']
tests_require = ['nose', 'httpretty', 'mock']

version = ''
with open('rz/__init__.py', 'r') as fd:
    version = re.search(r'^__version__\s*=\s*[\'"]([^\'"]*)[\'"]', 
                        fd.read(), re.MULTILINE).group(1)

if not version:
    raise RuntimeError('Cannot find version information')

setup(
    name='rz-python',
    version=version,
    description='CI/CD tool for Kubernetes 1.x',
    author_email="dineshyadav.iiit@gmail.com",
    author="Dinesh Yadav",
    url="https://github.com/dinesh/rz",
    packages=['rz'],
    license='MIT',
    entry_points={
        'console_scripts': [
            'rz = rz.cli:main'
        ]
    },
    install_requires=reqs,
    tests_require=tests_require,
    test_suite='nose.collector')