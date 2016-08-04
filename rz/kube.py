import pykube
import backports
import click
import time
import os
import json
import re
from six.moves.urllib.parse import urlencode

# Monkey-patch match_hostname with backports's match_hostname, allowing for IP addresses
# XXX: the exception that this might raise is
# backports.ssl_match_hostname.CertificateError
pykube.http.requests.packages.urllib3.connection.match_hostname = \
    backports.ssl_match_hostname.match_hostname

def get_entity(pykube_object):
    return globals()[pykube_object.kind](
        pykube_object.api,
        pykube_object.obj
    )


class Client:

    def __init__(self, context=None):
        self.context = context

    @property
    def api(self):
        config = pykube.KubeConfig.from_file(
            os.path.join(os.environ['HOME'], ".kube/config"))
        if self.context:
            config.set_current_context(self.context)

        return pykube.HTTPClient(config)

    def object(self, kube_object):
        underlying = getattr(pykube, kube_object['kind'])
        if not underlying:
            RuntimeError(
                "%s object is not supported by rz currently." %
                kube_object['kind'])

        return underlying.__call__(self.api, kube_object)

    def get_by_name(self, kind, name):
        return getattr(pykube, kind).objects(self.api).get_or_none(name=name)

    def get_deplopyment_revisions(self):
        revisions = []
        for dp in pykube.Deployment.objects(self.api):
            revision = _get_deployment_revision(dp.obj)
            if revision and revision not in revisions:
                revisions.append(revision)

        revisions.sort()
        return revisions


POD_NOT_READY   = 'not_ready'
POD_RUNNING     = 'running'
POD_FAILED      = 'failed'

PHASE_UNKNOWN   = 'unknown'
PHASE_RUNNING   = 'running'
PHASE_FAILED    = 'failed'
PHASE_SUCCEEDED = 'succeeded'

CONTAINER_ERROR_STATES = [
    'CrashLoopBackOff',
    'ImagePullBackOff',
    'ImageInspectError',
    'ErrImagePull',
    'ErrImageNeverPull',
    'RegistryUnavailable'
]


class ReplicaSet(pykube.ReplicaSet):

    def __init__(self, *args):
        pykube.ReplicaSet.__init__(self, *args)

    def pods(self):
        return pykube.Pod.objects(self.api).filter(
            namespace=self.namespace,
            selector=self.obj['spec']['selector']['matchLabels']
        )


class Event(pykube.objects.NamespacedAPIObject):
    version = 'v1'
    endpoint = 'events'
    kind = 'Event'


class Deployment(pykube.Deployment):

    def __init__(self, *args):
        pykube.Deployment.__init__(self, *args)

    def rollback(self, to_version=0):
        r_version = Event.objects(self.api).filter().response['metadata']['resourceVersion']

        rollback_object = dict(name=self.name, rollbackTo={'revision': to_version})
        r = self.api.post(**self.api_kwargs(
                operation='rollback',
                data=json.dumps(rollback_object)
        ))

        self.api.raise_for_status(r)
        done, success, error = False, False, None

        while not done:
            for event in Event.objects(self.api).filter(resource_version=r_version):
                ev = event.obj
                if ev['reason'] == 'DeploymentRollbackRevisionNotFound':
                    done, error = True, ev['message']
                else:
                    success = ev['reason'] == 'DeploymentRollback'
                    done    = success

            time.sleep(2)

        return success, error

    @property
    def revision(self):
        return _get_deployment_revision(self.obj)

    def reap(self):
        self.obj['spec']['replicas'] = 0
        self.update()

        while self.obj['spec']['replicas'] > 0:
            time.sleep(1)
            click.echo("checking pod status for %s" % self)
            self.reload()

    def check_status(self):
        match_selector = self.obj['spec']['selector']['matchLabels']
        failing_pods, status = [], PHASE_UNKNOWN

        while status == PHASE_UNKNOWN:
            running_count = 0
            ## check for newly created pods only ...
            deployment_pods = pykube.Pod.objects(self.api).filter(
                namespace=self.namespace,
                selector=match_selector)

            for pod in deployment_pods:
                pod = get_entity(pod)
                phase, error = pod.current_phase()

                click.echo("pod %s: phase: %s error: %s" % (pod, phase, error))

                if phase == POD_RUNNING:
                    running_count += 1
                elif phase == POD_FAILED:
                    status = PHASE_FAILED
                    failing_pods.append(pod)

            if running_count == self.obj['status']['replicas']:
                status = PHASE_RUNNING
            else:
                time.sleep(1)

        return failing_pods, status

class Pod(pykube.Pod):

    def __init__(self, *args):
        pykube.Pod.__init__(self, *args)

    def logs(self, container=None):
        """pykube doesn't have stable logs function yet"""

        url = self.api.url + "/api/{}/namespaces/{}/pods/{}/log".format(
            self.version, self.namespace, self.name
        )
        params = {}

        if container is not None:
            params['container'] = container

        query_string = urlencode(params)
        url += "?{}".format(query_string) if query_string else ""

        r = self.api.session.get(url=url)
        if r.headers.get('content-type') == 'application/json':
            return r.json()['message']

        return r.text

    def current_phase(self, sleep_for=1):
        status, error = POD_NOT_READY, None
        back_off_timeout = 10

        if self.ready:
            status = POD_RUNNING
            click.secho("All looks good for %s: %s" %
                        (self, self.obj['status']['conditions']), bg='green')
        else:
            if 'containerStatuses' in self.obj['status']:
                for container_status in self.obj['status']['containerStatuses']:
                    state = container_status['state']

                    if 'waiting' in state:
                        reason = state['waiting']['reason']
                        if reason in CONTAINER_ERROR_STATES:
                            status = POD_FAILED
                            error = state['waiting']['message']
                        elif reason == 'containercreating':
                            click.echo("{}: {}".format(
                                container_status['name'],
                                state['waiting']['message']
                            ))
                            time.sleep(back_off_timeout/2)

                    restart_count = container_status.get('restartCount', -1)

                    if restart_count > 3:
                        click.echo(
                            "Container restarting more than allowed: %s" %
                            container_status['name']
                        )
                        status = POD_FAILED
                    elif restart_count > 0:
                        click.echo("Container restarting : %s waiting for %s seconds before checking again" % (
                            container_status['name'], back_off_timeout))
                        time.sleep(back_off_timeout)
            else:
                click.echo("No status for %s" % self.name)

        return status, error

def _get_deployment_revision(obj):
    annotations = obj['metadata']['annotations']
    return annotations.get('deployment.kubernetes.io/revision')
