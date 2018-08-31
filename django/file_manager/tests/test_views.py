import tempfile
import json

from django.contrib.auth.models import AnonymousUser, User

from django.test import TestCase
from django.test import RequestFactory
from django.urls import reverse
from django.core.exceptions import PermissionDenied

from file_manager.views import FileBrowserView
from file_manager.filesystems.local import Local
from file_manager.permissions import Permissions

class TestFileBrowseView(TestCase):
    """
        Attributes:
            request: djanog.test.RequestFactory
    """
    @classmethod
    def setUpTestData(self):
        self.factory = RequestFactory()

        self.file_system = 'Local_FileSystem'
        storage = Local(name = self.file_system)
        storage.save()

        self.tempParentDir = tempfile.TemporaryDirectory(dir="/code/django/files/tmp/")
        self.tempFile = tempfile.NamedTemporaryFile(dir=self.tempParentDir.name)
        self.tempDir = tempfile.TemporaryDirectory(dir=self.tempParentDir.name)

        self.user = User.objects.create_user(
                            username='jacob',
                            email='jacob@…',
                            password='top_secret')

        Permissions.allow(self.user,storage, self.tempParentDir.name)

    def test_upload_file(self):
        with tempfile.NamedTemporaryFile(dir="/code/django/files/tmp/") as file:
            request = self.factory.post(
                reverse('file_manager:ajax', kwargs={ 'command':'browse'}),
                {
                  'storage_type': self.file_system,
                  'dir': self.tempParentDir.name
                },
                HTTP_X_REQUESTED_WITH='XMLHttpRequest'
            )
            request.user = self.user
            file.name = file.name.split("/")[-1] #Uploaded do not include the path
            request.FILES['file'] = file

            FileBrowserView.as_view()(request, command='upload')

    def test_access_denied(self):
        request = self.factory.post(
            reverse('file_manager:ajax', kwargs={ 'command':'browse'}),
            {
              'storage_type': self.file_system,
              'dir': '/'
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        request.user = self.user

        with self.assertRaises(PermissionDenied):
            FileBrowserView.as_view()(request, command='browse')

    def test_browse(self):
        request = self.factory.post(
            reverse('file_manager:ajax', kwargs={ 'command':'browse'}),
            {
              'storage_type': self.file_system,
              'dir': self.tempParentDir.name
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        request.user = self.user

        result = FileBrowserView.as_view()(request, command='browse')
        content = json.loads(result.content)

        self.assertEqual(result.status_code,200)
        self.assertEqual(content['dirs'],[self.tempDir.name.split("/")[-1]])
        self.assertEqual(content['files'][0]['name'],self.tempFile.name.split("/")[-1])
        self.assertEqual(content['files'][0]['size'],"0 Bytes")
