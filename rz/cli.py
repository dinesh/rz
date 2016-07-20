import click
from ConfigParser import ConfigParser
from operator import itemgetter
from rz import ComposeProject, KubeClient
import yaml
import os
import time
import pykube

config_filename = '.rz.ini'


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
def cli():
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


@cli.command(cls=InitCLI)
def init(*args, **kvargs):
    pass


@cli.command()
@click.option(
    '--skip',
    is_flag=True,
    default=False, help="Skip image generation on GCE cloudbuild")
@click.option('--out', '-o',
              type=click.Path(exists=True),
              default="gce.yml",
              help="path to kubernetes configration")
@click.option('--builder', '-b',
              default='google',
              type=click.Choice(['google', 'aws', 'local']))
def build(skip, out, builder):
    if builder == 'local':
        config = {}
    elif builder == 'google':
        config = get_gcs_config(ensure_keys=['project_id', 'zone'])

    elif builder == 'aws':
        raise NotImplementedError()

    project = ComposeProject(os.getcwd())
    project.build_with(builder, config, skip)
    project.save_for_k8(out)
    click.echo("kubernetes configuration saved at %s" % out)


@cli.command()
@click.option('--kube-config', '-k',
              default="gce.yml", help="path to kubernetes configration")
def up(kube_config):
    with open(kube_config, 'r') as fp:
        k8config = yaml.load_all(fp.read())

    k8client = KubeClient()
    for k8object in k8config:
        print "Starting service %s ..." % k8object['metadata']['name']
        k8client.object(k8object).create()

    click.echo("DONE !!")


@cli.command()
def down():
    client = KubeClient()
    for rc in pykube.Deployment.objects(client.api):
        click.echo("Deleting deployment %s" % rc)
        rc.delete()

    for svc in pykube.Service.objects(client.api):
        click.echo("Deleting service %s" % svc)
        svc.delete()


@cli.command()
@click.option('--config', '-c',
              default="gce.yml", help="path to kubernetes configration")
def deploy(config):
    with open(config, 'r') as fp:
        objects = yaml.load_all(fp.read())

    client = KubeClient()

    for _object in objects:
        kobject = client.object(_object)

        if kobject.exists():
            click.echo("Updating %s: %s" % (_object['kind'], kobject.name))
            kobject.update()
        else:
            click.echo("Creating %s: %s" %
                       (_object['kind'], _object['metadata']['name']))
            kobject.create()

    from rz import kube

    rollback = False

    for dp in pykube.Deployment.objects(client.api):
        dp = kube.Deployment(dp.api, dp.obj)
        failed_pods, status = dp.check_status()

        if status is not kube.PHASE_RUNNING:
            rollback = True
            assert len(failed_pods) > 0
            for pod in failed_pods:
                click.echo(
                  "{} failed to start because of".format(
                    pod.logs()['message']
                  )
                )

    if rollback:
        click.echo("Rolling back")

        for dp in pykube.Deployment.objects(client.api):
            dp = kube.Deployment(dp.api, dp.obj)
            dp.reap()

            click.echo("Deleting Deployment %s" % dp)
            dp.delete()

        for svc in pykube.Service.objects(client.api):
            click.echo("Deleting service %s" % svc)
            svc.delete()


def main():
    cli()

if __name__ == '__main__':
    main()
