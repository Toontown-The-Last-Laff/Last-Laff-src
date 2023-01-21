# Copyright (c) Pypperoni
#
# Pypperoni is licensed under the MIT License; you may
# not use it except in compliance with the License.
#
# You should have received a copy of the License with
# this source code under the name "LICENSE.txt". However,
# you may obtain a copy of the License on our GitHub here:
# https://github.com/Pypperoni/pypperoni
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific
# language governing permissions and limitations under the
# License.

from pypperoni.files import ConditionalFile, FileContainer
from pypperoni.module import Module, write_modules_file
from pypperoni.codeobj import write_frames_file
from pypperoni.util import safePrint

from threading import Thread, Lock
from Queue import Queue, Empty
import traceback
import hashlib
import math
import sys
import os

PYPPERONI_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class CMakeFileGenerator:
    def __init__(self, project, outputdir='build', nthreads=4):
        self.project = project
        self.outputdir = outputdir
        self.nthreads = nthreads

        self.modules = {}
        self.__files = []

        self.cmake_in_file = os.path.join(PYPPERONI_ROOT, 'pypperoni', 'cmake.in')
        self.add_directory(os.path.join(PYPPERONI_ROOT, 'python', 'Lib'))

        self.generate_codecs_index()

    def add_file(self, filename, name=None):
        '''
        Adds a single file to modules.
        If name is not provided, it's inferred from filename:
           path/to/file -> path.to.file
        '''
        with open(filename, 'rb') as f:
            data = f.read()

        if name is None:
            name = os.path.normpath(filename.rsplit('.', 1)[0]).replace(os.sep, '.')
            if name.endswith('.__init__'):
                name = name[:-9]

        self.add_module(name, data)

    def add_module(self, name, data):
        code = compile(data, name, 'exec')
        self.modules[name] = Module(name, code)

    def add_directory(self, path):
        '''
        Adds all Python files (.py) in a directory to modules.
        For example,
           dir1/
              file1.py
              file2.py

           will add the modules "file1" and "file2"
        '''
        cwd = os.getcwd()
        path = os.path.abspath(path)
        os.chdir(path)

        try:
            for root, _, filenames in os.walk('.'):
                for filename in filenames:
                    if filename.endswith('.py'):
                        self.add_file(os.path.join(root, filename))

        finally:
            os.chdir(cwd)

    def add_tree(self, path):
        '''
        Adds all Python files (.py) in a directory to modules, preserving the tree name.
        For example,
           tree1/
              file1.py
              file2.py

           will add the modules "tree1.file1" and "tree1.file2"
        '''
        cwd = os.getcwd()
        path = os.path.abspath(path)
        os.chdir(os.path.dirname(path))

        try:
            for root, _, filenames in os.walk(os.path.basename(path)):
                for filename in filenames:
                    if filename.endswith('.py'):
                        self.add_file(os.path.join(root, filename))

        finally:
            os.chdir(cwd)

    def generate_codecs_index(self):
        data = 'from encodings import register_mod\n'
        for k in self.modules:
            if k.startswith('encodings.'):
                name = k[10:]
                data += 'from encodings import %s\n' % name
                data += 'register_mod(%s)\n' % name

        self.add_module('codecs_index', data)

    @staticmethod
    def hash_file(f):
        hash = hashlib.sha256()
        while True:
            data = f.read(8192)
            if not data:
                break
            hash.update(data)

        return hash.hexdigest()[:7]

    def __process_one(self, name, module):
        prefix = os.path.join(self.outputdir, 'gen', 'modules', name)
        f = FileContainer(prefix, self.hash_file)
        module.generate_c_code(f, self.modules)
        with Lock():
            self.__files.extend(os.path.join('modules', os.path.basename(x[0])) for x in f.close())

    def __worker(self):
        while True:
            try:
                name, module, text = self.__queue.get_nowait()

            except Empty:
                break

            safePrint(text)
            error = False
            try:
                self.__process_one(name, module)

            except:
                sys.stderr.write('Exception in thread')
                error = True
                sys.stderr.write(traceback.format_exc())

            finally:
                self.__queue.task_done()
                if error:
                    sys.stdout.flush()
                    sys.stderr.flush()
                    os._exit(1)

    def run(self):
        modules_dir = os.path.join(self.outputdir, 'gen', 'modules')
        if not os.path.isdir(modules_dir):
            os.makedirs(modules_dir)

        self.__queue = Queue()
        total = len(self.modules)
        n = int(math.ceil(math.log(total, 10)))
        _format = '[%%%dd/%%%dd] %%s' % (n, n)
        i = 0
        for name, module in self.modules.items():
            i += 1
            text = _format % (i, total, name)
            self.__queue.put((name, module, text))

        for i in xrange(self.nthreads):
            t = Thread(target=self.__worker)
            t.daemon = True
            t.start()

        self.__queue.join()

        filename = os.path.join(self.outputdir, 'gen', 'modules.I')
        f = ConditionalFile(filename, self.hash_file)
        write_modules_file(f, self.modules)
        self.__files.append(os.path.basename(f.close()[0]))

        filename = os.path.join(self.outputdir, 'gen', 'frames.I')
        f = ConditionalFile(filename, self.hash_file)
        write_frames_file(f)
        self.__files.append(os.path.basename(f.close()[0]))

        files = ''
        for filename in self.__files:
            files += '     %s\n' % os.path.join('gen', filename).replace('\\', '/')

        with open(self.cmake_in_file, 'rb') as f:
            cmakein = f.read()

        cmakein = cmakein.replace('$$project$$', self.project)
        cmakein = cmakein.replace('$$files$$', files)
        cmakein = cmakein.replace('$$pypperoni_root$$', PYPPERONI_ROOT.replace('\\', '/'))

        with open(os.path.join(self.outputdir, 'CMakeLists.txt'), 'wb') as f:
            f.write(cmakein)
