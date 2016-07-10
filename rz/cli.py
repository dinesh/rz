import click

@click.group()
def cli():
  pass

@cli.command()
@click.option('--project-id', required=True, help="GCE Project Id")
@click.option('--cluster', required=True, help="GCE Container Cluster Name")
@click.option('--zone', default='asia-east1-a', help="GCE region/zone")
@click.option('--compose-config', '-c', default="docker-compose.yml", help="path to compose configuration file")
def stop(project_id, zone, cluster, compose_config):
  from rz.gce import GCEKubeClient
  import pykube

  kube_client  = GCEKubeClient.from_project(project_id, zone, cluster)
  for rc in pykube.ReplicationController.objects(kube_client.api):
    rc.delete()

@cli.command()
@click.option('--project-id', required=True, help="GCE Project Id")
@click.option('--cluster', required=True, help="GCE Container Cluster Name")
@click.option('--zone', default='asia-east1-a', help="GCE region/zone")
@click.option('--compose-config', '-c', default="docker-compose.yml", help="path to compose configuration file")
@click.option('--kube-config', '-k', default="gce.yml", help="path to kubernetes configration")
def start(project_id, cluster, zone, compose_config, kube_config):
  from rz.docker import ComposeProject
  from rz.gce import GCEKubeClient

  project = ComposeProject.from_file(project_id, compose_config, zone=zone)
  k8config = project.kube_configuration
  project.build()
  project.save(k8config, kube_config)

  k8client = GCEKubeClient.from_project(project_id, zone, cluster)
  for k8object in k8config:
    print "Starting service %s ..." % k8object['metadata']['name']
    k8client.object(k8object).create()

  click.echo("DONE !!")

def main():
  cli(obj={})

if __name__ == '__main__':
  main()

