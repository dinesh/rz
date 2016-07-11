import click

@click.group()
def cli():
  pass

@cli.command()
@click.option('--project-id', required=True, help="GCE Project Id")
@click.option('--cluster', required=True, help="GCE Container Cluster Name")
@click.option('--zone', default='asia-east1-a', help="GCE region/zone")
def stop(project_id, zone, cluster):
  from rz.gce import GCEKubeClient
  import pykube

  kube_client  = GCEKubeClient.from_project(project_id, zone, cluster)

  for rc in pykube.Deployment.objects(kube_client.api):
    print rc
    rc.delete()

  for svc in pykube.Service.objects(kube_client.api):
    print svc
    svc.delete()

  for pod in pykube.Pod.objects(kube_client.api):
    print pod
    pod.delete()

@cli.command()
@click.option('--project-id', required=True, help="GCE Project Id")
@click.option('--zone', default='asia-east1-a', help="GCE region/zone")
@click.option('--compose-config', '-c', default="docker-compose.yml", help="path to compose configuration file")
@click.option('--kube-config', '-k', default="gce.yml", help="path to kubernetes configration")
def build(project_id, zone, compose_config, kube_config):
  from rz.docker import ComposeProject
  from rz.gce import GCEKubeClient

  project = ComposeProject.from_file(project_id, compose_config, zone=zone)
  project.build()
  project.save(kube_config)

  click.echo("DONE !!")

@cli.command()
@click.option('--project-id', required=True, help="GCE Project Id")
@click.option('--cluster', required=True, help="GCE Container Cluster Name")
@click.option('--zone', default='asia-east1-a', help="GCE region/zone")
@click.option('--kube-config', '-k', default="gce.yml", help="path to kubernetes configration")
def start(project_id, cluster, zone, kube_config):
  from rz.docker import ComposeProject
  from rz.gce import GCEKubeClient
  import yaml

  with open(kube_config, 'r') as fp:
    k8config = yaml.load_all(fp.read())

  k8client = GCEKubeClient.from_project(project_id, zone, cluster)
  for k8object in k8config:
    print "Starting service %s ..." % k8object['metadata']['name']
    k8client.object(k8object).create()

  click.echo("DONE !!")

def main():
  cli(obj={})

if __name__ == '__main__':
  main()

