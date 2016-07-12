import click
from ConfigParser import ConfigParser
from operator import itemgetter
from rz import ComposeProject, GCEKubeClient
import yaml, os
import pykube

def get_config(section='config'):
  parser = ConfigParser()
  parser.read('.gce.ini')

  if parser.has_section(section) is False:
    raise RuntimeError('Please run rz init to setup project')

  result, options = {}, parser.options(section)

  for option in options:
    try:
      result[option] = parser.get(section, option)
    except:
      result[option] = None

  return result

@click.group()
def cli():
  pass

@cli.command()
@click.argument('path')
@click.option('--project-id', help="GCE Project Id")
@click.option('--cluster', help="GCE Container Cluster Name")
@click.option('--zone', help="GCE region/zone")
def init(path, project_id, cluster, zone):
  parser = ConfigParser()
  config_path = os.path.join(path, '.gce.ini')
  parser.read(config_path)

  if parser.has_section('config'):
    project_id = project_id or parser.get('config','project_id')
    zone       = zone or parser.get('config','zone')
    cluster    = cluster or parser.get('config','cluster')
  else:
    parser.add_section('config')

  if project_id is None:
    project_id = click.prompt('Please enter gce project id')
    parser.set('config','project_id', project_id)

  if zone is None:
    zone = click.prompt('Please enter gce zone')
    parser.set('config','zone', zone)

  if cluster is None:
    cluster = click.prompt('Please enter gce cluster name')
    parser.set('config','cluster', cluster)

  with open(config_path, 'w') as configfile:
    parser.write(configfile)

@cli.command()
@click.option('--kube-config', '-k', default="gce.yml", help="path to kubernetes configration")
@click.option('--skip', default=False, help="Skip gce cloudbuild")
def build(kube_config, skip):
  project_id, zone, cluster = itemgetter('project_id', 'zone', 'cluster')(get_config())
  project = ComposeProject.from_file(project_id, os.getcwd(), zone=zone)
  project.build(skip)
  project.save(kube_config)
  click.echo("kubernetes saved at %s" % kube_config)

@cli.command()
@click.option('--kube-config', '-k', default="gce.yml", help="path to kubernetes configration")
def start(kube_config):
  project_id, zone, cluster = itemgetter('project_id', 'zone', 'cluster')(get_config())

  with open(kube_config, 'r') as fp:
    k8config = yaml.load_all(fp.read())

  k8client = GCEKubeClient.from_project(project_id, zone, cluster)
  for k8object in k8config:
    print "Starting service %s ..." % k8object['metadata']['name']
    k8client.object(k8object).create()

  click.echo("DONE !!")


@cli.command()
def stop():
  project_id, zone, cluster = itemgetter('project_id', 'zone', 'cluster')(get_config())
  kube_client  = GCEKubeClient.from_project(project_id, zone, cluster)

  for rc in pykube.Deployment.objects(kube_client.api):
    click.echo("Deleting deployment %s" % rc)
    rc.delete()

  for svc in pykube.Service.objects(kube_client.api):
    click.echo("Deleting service %s" % svc)
    svc.delete()

def main():
  cli()

if __name__ == '__main__':
  main()

