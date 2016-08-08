import click
from ConfigParser import ConfigParser
from operator import itemgetter
from rz import ComposeProject, kube
import yaml, os, sys, time
import pykube

config_filename = '.rz.ini'
allow_history   = True

def get_config(section):
    parser = ConfigParser()
    parser.read(config_filename)

    if parser.has_section(section) is False:
        raise ValueError('Section not found %s in %s' %
                         (section, config_filename))

    result, options = {}, parser.options(section)

    for option in options:
        try:
            result[option] = parser.get(section, option)
        except:
            result[option] = None

    return result


def get_gcs_config(ensure_keys=[]):
    result = get_config('gce')
    for key in ensure_keys:
        assert result[key]
    return result


@click.group()
def deployer():
    pass

@click.group()
def builder():
    pass

class InitCLI(click.MultiCommand):

    def list_commands(self, ctx):
        providers = ['gce', 'docker']
        providers.sort()
        return providers

    def get_command(self, ctx, cmd):
        if cmd == 'gce':
            options = [
                click.Argument(('path',)),
                click.Option(('--project-id',)),
                click.Option(('--cluster',)),
                click.Option(('--zone',))
            ]

            def gce_init(path, project_id, cluster, zone):
                parser = ConfigParser()
                config_path = os.path.join(path, config_filename)
                parser.read(config_path)

                if parser.has_section('gce'):
                    project_id = project_id or parser.get('gce', 'project_id')
                    zone = zone or parser.get('gce', 'zone')
                    cluster = cluster or parser.get('gce', 'cluster')
                else:
                    parser.add_section('gce')

                if project_id is None:
                    project_id = click.prompt('Please enter gce project id')
                    parser.set('gce', 'project_id', project_id)

                if zone is None:
                    zone = click.prompt('Please enter gce zone')
                    parser.set('gce', 'zone', zone)

                if cluster is None:
                    cluster = click.prompt('Please enter gce cluster name')
                    parser.set('gce', 'cluster', cluster)

                with open(config_path, 'w') as configfile:
                    parser.write(configfile)

            return click.Command(cmd, params=options, callback=gce_init)
        else:
            def docker_init(path):
                pass
            return click.Command(cmd,
                                 params=[click.Argument(("path",))],
                                 callback=docker_init)


@builder.command(cls=InitCLI)
def init(*args, **kvargs):
    pass

@builder.command()
@click.option('--builder', '-b',
              default='local',
              type=click.Choice(['google', 'aws', 'local']))
@click.option('--out', '-o',
              type=click.Path(),
              default="deploy.yml",
              help="path to kubernetes configration")
@click.option('--namespace', '-n',
              default='default',
              help='kubernetes namespace to use.')
@click.option('--skip', is_flag=True,
              default=False,
              help="Skip image building")
@click.option('--gce-project-id', help="Google Cloud Project Id")
@click.option('--gce-zone', help="Google Cloud Zone", default="us-central1-a")
@click.option('--registry', help="URL for docker registry to push")
@click.option('--username', help="Username for docker registry")
@click.option('--password', help="Password for docker registry")
def build(builder, out, namespace, skip, gce_project_id, gce_zone, 
    registry, username, password, tag):

    config = {}
    if builder == 'google':
        assert gce_project_id
        assert gce_zone
        config["project_id"] = gce_project_id
        config['zone']       = gce_zone
    
    if builder == 'aws':
        raise NotImplementedError()

    project = ComposeProject(os.getcwd())
    built_images = project.build_with(builder, config, skip)

    if registry:
        for image in built_images:
            project.push_to_registry(registry, username, password, image)

    parsed_config = project.kube_objects(namespace=namespace)
    project.save_for_k8(out, parsed_config)
    click.echo("kubernetes configuration saved at %s" % out)

@deployer.command()
@click.option('--path', '-p',
              default="gce.yml",
              type=click.Path(exists=True),
              help="path to kubernetes configration")
def start(path):
    with open(path, 'r') as fp:
        k8config = yaml.load_all(fp.read())

    k8client = kube.Client()
    for k8object in k8config:
        print "Starting service %s ..." % k8object['metadata']['name']
        k8client.object(k8object).create()

    click.echo("DONE !!")

@deployer.command()
def stop():
    client = kube.Client()
    for dp in pykube.Deployment.objects(client.api):
        dp = kube.get_entity(dp)
        dp.reap()

        click.echo("Deleting deployment %s" % dp)
        dp.delete()

    for svc in pykube.Service.objects(client.api):
        click.echo("Deleting service %s" % svc)
        svc.delete()

@click.option('--context', '-c',
              default="localkube",
              help="kubernetes cluster context to use")
@click.option('--revision',
              default=0,
              help='revision number to rollback.')
@deployer.command()
def rollback(context, revision):
    client = kube.Client(context)
    revisions = client.get_deplopyment_revisions()

    if len(revisions) > 0:
        if len(revisions) > 1:
            click.echo(
                "Cluster seems to have multiple revisions: {},\
                 Aborting deployment.".format(revisions))
            sys.exit(1)
        else:
            click.echo("Detected deployed version: %s" % revisions[0])

    to_revision = revisions[0]
    click.secho("Rolling back to revision: %s" % to_revision, bold=True)

    for dp in pykube.Deployment.objects(client.api):
        dp = kube.get_entity(dp)
        rolled_back, error = dp.rollback(to_revision)
        if rolled_back:
            click.echo("->> Rolled back successfully.")
        else:
            click.echo("->> Rolling failed b/c of error: %s" % error)
            sys.exit(1)

@click.option('--context', '-c',
              default="localkube",
              help="kubernetes cluster context to use")
@click.option('--path', '-p',
              default="deploy.yml",
              type=click.Path(exists=True),
              help="path to kubernetes configration")
@click.option('--rollback/--no-rollback',
              is_flag=True, default=False,
              help='rollback the deploy if not successful.')
@click.option('--revision',
              default=0,
              help='rollback to revision number (only Deployment is supported).')
@deployer.command()
def deploy(context, path, rollback, revision):
    with open(path, 'r') as fp:
        objects = list(yaml.load_all(fp.read()))

    client = kube.Client(context)
    revisions = client.get_deplopyment_revisions()

    new_revision = 0
    if len(revisions) > 0:
        if len(revisions) > 1:
            click.echo(
                ("Cluster seems to have multiple deployment revisions: {}, "
                 "Aborting deployment.").format(revisions))
            sys.exit(1)
        else:
            new_revision = int(revisions[0]) + 1
            click.echo("Detected deployed version: %s" % revisions[0])

    for kind in [o['kind'] for o in objects]:
        if kind not in ['Deployment', 'ReplicationController', 'ReplicaSet', 'Pod', 'Service']:
            raise ValueError("rzb doesn't handle object of type: %s" % kind)

    ordered_objects = [o for o in objects if o['kind'] == 'Pod'] + \
          [o for o in objects if o['kind'] == 'Deployment'] + \
          [o for o in objects if o['kind'] == 'ReplicationController'] + \
          [o for o in objects if o['kind'] == 'ReplicaSet'] + \
          [o for o in objects if o['kind'] == 'Service']

    [_apply_object(client, o, new_revision) for o in ordered_objects]

    deployement_failed = False
    time.sleep(2)

    for dp in pykube.Deployment.objects(client.api):
        dp = kube.get_entity(dp)
        failed_pods, status = dp.check_status()

        if status is not kube.PHASE_RUNNING:
            deployement_failed = True
            for pod in failed_pods:
                click.secho(pod.logs(), bg='red')

    if deployement_failed and rollback:
        to_revision = revision
        click.secho("Rolling back to last revision: %s" % to_revision, bold=True)

        for dp in pykube.Deployment.objects(client.api):
            dp = kube.get_entity(dp)
            rolled_back, error = dp.rollback(to_revision)
            if rolled_back:
                click.echo("->> Rolled back successfully.")
            else:
                click.echo("->> Rolling failed b/c of error: %s" % error)
                sys.exit(1)
    else:
        click.echo("->> SUCCESS")

RZD_VERSION_KEY = 'rzd/revision'

def _apply_object(client, _json, revision):
    live_object = client.get_by_name(_json['kind'], _json['metadata']['name'])

    annotations = _json['metadata'].get('annotations', {})
    annotations[RZD_VERSION_KEY] = str(revision)
    _json['metadata']['annotations'] = annotations
    n_object = client.object(_json)

    if live_object:
        click.echo("Updating %s: %s" % (_json['kind'], n_object.name))
        n_object.update()
    else:
        click.echo("Creating %s: %s" %
                   (_json['kind'], _json['metadata']['name']))
        n_object.create()

if __name__ == '__main__':
    deployer()
