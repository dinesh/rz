
RZ ( RazorCloud)
----------------

`RZ` provides cli tools for apps/microservices to build and deploy on [kubernetes](http://kubernetes.io/) easily. In nutshell it has two tools -

`rzb` is a tool to help users familiar with docker-compose or heroku move to Kubernetes. It takes a Docker Compose file and translates it into Kubernetes objects, it can then submit those objects to a Kubernetes endpoint with the `rzd deploy` command.

`rzd` is CI/CD tool to deploy your app to kubernetes cluster. It supports deployment history, automatic rollback, and versioned kubernetes objects. In short it's a better alternative of `kubectl apply -f`

The project's are to -

* Enable rapid iteration with Kubernetes
* Be the fastest, simplest way to deploy Docker to production
* help tremendously to start Kubernetizing your application

Installation
------------
To install `rz`, use pip:
  
    $ pip install rz-python
 OR
 
    $ pip install git+https://github.com/dinesh/rz.git 
    
 RZB cli
---------
`rzb` can generate kubernetes objects from `docker-compose`, heroku `app.json` and docker bundles (`DAB`)  and build docker/rkt images from source using local or remote image builder (local docker daemon, Google cloudbuilder or amazon ECS )

Usage
------
  To use rzb, you should create [compose](https://docs.docker.com/compose/compose-file/) `(docker-compose.yml)` and define your app's dependencies. Supported options

    => rzb build --help
    Usage: rzb build [OPTIONS]

    Options:
      -b, --builder [google|aws|local]
      -o, --out PATH                  output path of kubernetes configration
      -n, --namespace TEXT            kubernetes namespace to use(otherwise default)
      --skip                          Skip image building
      --gce-project-id TEXT           Google Cloud Project Id(to build with GCB)
      --gce-zone TEXT                 Google Cloud Zone(to build with GCB)
      --help                          Show this message and exit.

**Unsupported docker-compose configuration options**
  
  Currently `rzb` does not support the following Docker Compose options -

    'cap_add', 'cap_drop', 'cgroup_parent', 'container_name', 'devices', 'depends_on', 'dns', 'dns_search', 'tmpfs', 'extends',
    'external_links', 'extra_hosts', 'labels', 'links', 'logging'
    
  It supports both `v1` and `v2` vesion of docker compose.

RZD cli
------

`rzd` is a tool to deploy and manage application stacks on kubernetes. It wait for successful deployment, check all pod's status and will rollback in case of any failure. It uses [Deployment](http://kubernetes.io/docs/user-guide/deployments/#what-is-a-deployment) and rollout history introduced in kubernetes 1.2 under the hood.

Usage
-----

To use `rzd` you should have a kubernetes cluster up and running, you can verify with 
  
    $ kubectl cluster-info

To Deploy your application

    => rzd deploy --help
    Usage: rzd deploy [OPTIONS]

    Options:
      --revision INTEGER          revision number to rollback.
      --rollback / --no-rollback  rollback the deploy if not successful.
      -p, --path PATH             path to kubernetes configration
      -c, --context TEXT          kubernetes cluster context to use
      --help                      Show this message and exit.


Contributing
------------

We'd love to see your contributions - please see the CONTRIBUTING file for guidelines on how to contribute.

Reporting bugs
--------------

If you haven't already, it's worth going through [Elika Etemad's](http://fantasai.inkedblade.net/style/talks/filing-good-bugs/) guide for good bug reporting. In one sentence, good bug reports should be both reproducible and specific.
