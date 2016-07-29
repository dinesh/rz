import os
import time
import tempfile
import tarfile
from googleapiclient import discovery, http
from googleapiclient.errors import HttpError
from oauth2client.client import GoogleCredentials


def archive_codebase(path, project_id, bucket=None):
    project_id = project_id or os.getenv('GCP_PROJECT_ID')
    if project_id is None:
        raise RuntimeError("Missing project_id.")

    if bucket is None:
        bucket = '%s-cbstorage' % project_id

    archive = tempfile.NamedTemporaryFile(delete=False, suffix='.tar.gz')
    tar = tarfile.open(fileobj=archive, mode='w:gz')
    print "Archiving %s to %s" % (path, archive.name)

    for spath, subdirs, files in os.walk(path):
        for name in files:
            print 'Adding %s' % os.path.relpath(
                os.path.join(spath, name), path)

            tar.add(os.path.join(spath, name), recursive=False,
                    arcname=os.path.relpath(os.path.join(spath, name), path))

    tar.close()
    archive.close()

    return bucket, archive


def upload_to_gcr(project_id, bucket, archive):
    source_key = os.path.basename(archive.name)

    print 'Checking for bucket %s...' % bucket
    credentials = GoogleCredentials.get_application_default()

    gcs_service = discovery.build('storage', 'v1', credentials=credentials)
    req = gcs_service.buckets().get(bucket=bucket)

    try:
        req.execute()
    except HttpError, error:
        if error.resp.status == 404:
            print 'Bucket %s not found, attempting to create it...' % bucket
            req = gcs_service.buckets().insert(
                project=project_id, body={'name': bucket})
            resp = req.execute()
        else:
            raise error

    print 'Uploading %s to %s...' % (source_key, bucket)

    try:
        body = {'name': source_key}
        media = http.MediaFileUpload(
            archive.name, mimetype='application/x-gzip', chunksize=4194304, resumable=True)
        req = gcs_service.objects().insert(
            bucket=bucket,
            name=source_key,
            media_body=media,
            body={"cacheControl": "public,max-age=31536000"}
        )
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status:
                print "Uploaded %d%%." % int(status.progress() * 100)
        print '...done!'
    except HttpError, error:
        if error.resp.status == 403:
            raise Exception(
                "You don\'t have permission to write to GCS bucket %s. \
                Fix this or specify a different bucket to use." %
                bucket)
        else:
            raise error

    return source_key


def build_from_gcr(project_id, bucket, source_key, image_uri, build_options={}):
    # Invoke the container builder API
    cb_request_body = {
        "source": {
            "storageSource": {
                "bucket": bucket,
                "object": source_key,
            }
        },
        "steps": [
            {
                "name": "gcr.io/cloud-builders/dockerizer",
                "args": [image_uri],
                "env": [
                    "DOCKERFILE=%s" % build_options.get(
                        'dockerfile', 'Dockerfile')
                ]
            }
        ],
        "images": [image_uri]
    }

    credentials = GoogleCredentials.get_application_default()
    ccb_service = discovery.build('cloudbuild', 'v1', credentials=credentials,
                                  discoveryServiceUrl="https://content-cloudbuild.googleapis.com/\
                      $discovery/rest?version=v1")

    req = ccb_service.projects().builds().create(
        projectId=project_id, body=cb_request_body)

    resp = req.execute()

    if resp['metadata']['build']['status'] in ['QUEUED', 'QUEUING']:
        print 'Queued build %s' % resp['metadata']['build']['id']

        operation_id = resp['name']
        while resp['metadata']['build']['status'] in ['QUEUED', 'QUEUING', 'WORKING']:
            resp = ccb_service.operations().get(name=operation_id).execute()
            print 'Building... %s' % resp['metadata']['build']['status']
            time.sleep(2)

    if resp['metadata']['build']['status'] == 'SUCCESS':
        resp = ccb_service.operations().get(name=operation_id).execute()
        for image in resp['metadata']['build']['results']['images']:
            print 'Built %s' % image['name']
            print '(Image digest: %s)' % image['digest']
    else:
        print 'Build returned %s - check build ID %s' % (
            resp['metadata']['build']['status'],
            resp['metadata']['build']['id'])
