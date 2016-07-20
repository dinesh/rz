import pykube
import click
import time
from six.moves.urllib.parse import urlencode

POD_NOT_READY = 'not_ready'
POD_RUNNING = 'running'
POD_FAILED = 'failed'

PHASE_UNKNOWN = 'unknown'
PHASE_RUNNING = 'running'
PHASE_FAILED = 'failed'


class ReplicaSet(pykube.ReplicaSet):

    def __init__(self, *args):
        pykube.ReplicaSet.__init__(self, *args)

    def pods(self):
        return pykube.Pod.objects(self.api).filter(
            namespace=self.namespace,
            selector=self.obj['spec']['selector']['matchLabels']
        )


class Deployment(pykube.Deployment):

    def __init__(self, *args):
        pykube.Deployment.__init__(self, *args)

    def replica_sets(self):
        return pykube.ReplicaSet.objects(self.api).filter(
            namespace=self.namespace,
            selector=self.obj['spec']['selector']['matchLabels']
        )

    def reap(self):
        click.echo("Scaling pods down for %s" % self)

        self.obj['spec']['replicas'] = 0
        self.update()

        while self.obj['spec']['replicas'] > 0:
            time.sleep(1)
            click.echo("checking pod status for %s" % self)
            self.reload()

    def check_status(self):
        match_selector = self.obj['spec']['selector']['matchLabels']
        failing_pods, status, replica_count = [], PHASE_UNKNOWN, 0

        while status == PHASE_UNKNOWN:
            running_pods = pykube.Pod.objects(self.api).filter(
                namespace=self.namespace, selector=match_selector)

            for pod in running_pods:
                pod = Pod(pod.api, pod.obj)
                pod_state, error = pod.current_status()

                if pod_state == POD_RUNNING:
                    replica_count += 1
                elif pod_state == POD_FAILED:
                    status = PHASE_FAILED
                    failing_pods.append(pod)

            if replica_count == self.obj['spec']['replicas']:
                status = PHASE_RUNNING
            else:
                time.sleep(1)

        return failing_pods, status


class Pod(pykube.Pod):

    def __init__(self, *args):
        pykube.Pod.__init__(self, *args)

    def logs(self, container=None):
        url = self.api.url + "/api/{}/namespaces/{}/pods/{}/log".format(
          self.version, self.namespace, self.name
        )
        params = {}

        if container is not None:
            params['container'] = container

        query_string = urlencode(params)
        url += "?{}".format(query_string) if query_string else ""

        r = self.api.session.get(url=url)
        return r.json()

    def current_status(self, sleep_for=1):
        status, error = POD_NOT_READY, None

        if self.ready:
            status = POD_RUNNING
            click.echo("All looks god for %s" % self)
        else:
            if 'containerStatuses' in self.obj['status']:
                for container_status in self.obj['status']['containerStatuses']:
                    state = container_status['state']

                    if 'waiting' in state and \
                            state['waiting']['reason'].lower() in ['imagepullbackoff', 'errimagepull']:

                        status = POD_FAILED
                        error = state['waiting']['message']

                    restart_count = state.get('restartCount', -1)
                    if restart_count > 3:
                        clik.echo(
                            "Container restarting more than allowed: %" %
                            status['name']
                        )
                        status = POD_FAILED
                    elif restart_count > 0:
                        click.echo("Container restarting : %" % status['name'])
            else:
                click.echo("No status for %s" % self.name)

        return status, error

    def isReady(self):
        ready = False

        if 'status' in self.obj and 'conditions' in self.obj['status']:
            for cond in self.obj['status']['conditions']:
                if cond['type'] == 'Ready':
                    ready = cond['status'] == 'True'

        return ready
