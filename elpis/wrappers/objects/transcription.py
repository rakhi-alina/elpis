from pathlib import Path
from elpis.wrappers.input.resample import resample
from elpis.wrappers.objects.command import run
from elpis.wrappers.objects.fsobject import FSObject
# import shutil
import threading
import subprocess
from typing import Callable
import os
import distutils.dir_util

class Transcription(FSObject):
    _config_file = "transcription.json"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.audio_file_path = self.path.joinpath('audio.wav')
        self.model = None
        self.config["model_name"] = None
        self.config["status"] = "ready"
        self.status = "ready"
        self.type = None

    @classmethod
    def load(cls, base_path: Path):
        self = super().load(base_path)
        self.audio_file_path = self.path.joinpath('audio.wav')
        self.model = None
        return self

    def link(self, model):
        self.model = model
        self.config['model_name'] = model.name

    @property
    def status(self):
        return self.config['status']

    @status.setter
    def status(self, value: str):
        self.config['status'] = value

    # builds the infer files in the state transcription dir,
    def _cook_generate_infer_files(self):
        # cook the infer file generator
        # TODO fix below
        with open('/elpis/elpis/wrappers/inference/generate-infer-files.sh', 'r') as fin:
            generator: str = fin.read()
        generator = generator.replace('working_dir/input/infer', f'{self.path}')
        generator = generator.replace('working_dir/input/output/kaldi/data/test',
                                      f"{self.model.path.joinpath('kaldi', 'data', 'test')}")
        generator = generator.replace('working_dir/input/output/kaldi/data/infer',
                                      f"{self.model.path.joinpath('kaldi', 'data', 'infer')}")
        generator_file_path = self.path.joinpath('gen-infer-files.sh')
        with generator_file_path.open(mode='w') as fout:
            fout.write(generator)
        run(f'chmod +x {generator_file_path}')
        run(f'{generator_file_path}')
        print("")

    def _process_audio_file(self, audio):
        # copy audio to the tmp folder for resampling
        tmp_path = Path(f'/tmp/{self.hash}')
        tmp_path.mkdir(parents=True, exist_ok=True)
        tmp_file_path = tmp_path.joinpath('original.wav')
        # if isinstance(audio, Path) or isinstance(audio, str):
        #     shutil.copy(f'{audio}', f'{tmp_file_path}')
        # elif isinstance(audio, BufferedIOBase):
        with tmp_file_path.open(mode='wb') as fout:
            fout.write(audio.read())
        # resample the audio file
        resample(tmp_file_path, self.path.joinpath('audio.wav'))

    def _bake_gmm_decode_align(self):
        with open('/elpis/elpis/wrappers/inference/gmm-decode-align.sh', 'r') as fin:
            content: str = fin.read()
        content = content.replace('../../../../kaldi_helpers/output/ctm_to_textgrid.py',
                                  '/elpis/elpis/wrappers/output/ctm_to_textgrid.py')
        content = content.replace('../../../../kaldi_helpers/output/textgrid_to_elan.py',
                                  '/elpis/elpis/wrappers/output/textgrid_to_elan.py')
        decode_file_path = self.path.joinpath('gmm-decode-align.sh')
        with decode_file_path.open(mode='w') as file_:
            file_.write(content)
        run(f'chmod +x {decode_file_path}')

        p = subprocess.run(f'sh {decode_file_path}'.split(), cwd=f'{self.model.path.joinpath("kaldi")}', check=True)

    def transcribe(self, on_complete: Callable=None):
        self.status = "transcribing"
        self.type = "text"
        kaldi_infer_path = self.model.path.joinpath('kaldi', 'data', 'infer')
        kaldi_test_path = self.model.path.joinpath('kaldi', 'data', 'test')
        kaldi_path = self.model.path.joinpath('kaldi')
        os.makedirs(f"{kaldi_infer_path}", exist_ok=True)
        distutils.dir_util.copy_tree(f'{self.path}', f"{kaldi_infer_path}")
        distutils.file_util.copy_file(f'{self.audio_file_path}', f"{self.model.path.joinpath('kaldi', 'audio.wav')}")

        subprocess.run('sh /elpis/elpis/wrappers/inference/gmm-decode.sh'.split(),
                       cwd=f'{self.model.path.joinpath("kaldi")}', check=True)

        # move results
        cmd = f"cp {kaldi_infer_path}/one-best-hypothesis.txt {self.path}/ && "
        cmd += f"infer_audio_filename=$(head -n 1 {kaldi_test_path}/wav.scp | awk '{{print $2}}' |  cut -c 3- ) && "
        cmd += f"cp \"{kaldi_path}/$infer_audio_filename\" {self.path}"
        run(cmd)
        self.status = "transcribed"
        if on_complete is not None:
            on_complete()

    def transcribe_align(self, on_complete: Callable=None):

        def transcribe():
            kaldi_infer_path = self.model.path.joinpath('kaldi', 'data', 'infer')
            os.makedirs(f"{kaldi_infer_path}", exist_ok=True)
            distutils.dir_util.copy_tree(f'{self.path}', f"{kaldi_infer_path}")
            distutils.file_util.copy_file(f'{self.audio_file_path}', f"{self.model.path.joinpath('kaldi', 'audio.wav')}")

            self._bake_gmm_decode_align()
            # p = subprocess.run('sh /kaldi-helpers/kaldi_helpers/inference/gmm-decode-align.sh'.split(),
            # cwd=f'{self.model.path.joinpath("kaldi")}')

            # move results
            # cmd = f"cp {kaldi_infer_path}/one-best-hypothesis.txt {self.path}/ && "
            # cmd += f"infer_audio_filename=$(head -n 1 {kaldi_test_path}/wav.scp | awk '{{print $2}}' |  cut -c 3- ) && "
            # cmd += f"cp \"{kaldi_path}/$infer_audio_filename\" {self.path}"
            # run(cmd)
            distutils.file_util.copy_file(f"{kaldi_infer_path.joinpath('utterance-0.eaf')}", f'{self.path}/{self.hash}.eaf')
            self.status = "transcribed"

        def transcribe_in_background():
            transcribe()
            on_complete()

        self.status = "transcribing"
        self.type = "elan"
        if on_complete is None:
            transcribe()
        else:
            t = threading.Thread(target=transcribe_in_background)
            t.start()

    def prepare_audio(self, audio, on_complete: Callable=None):
        self._process_audio_file(audio)
        self._cook_generate_infer_files()
        if on_complete is not None:
            on_complete()

    def text(self):
        with open(f'{self.path}/one-best-hypothesis.txt', 'rb') as fin:
            return fin.read()

    def elan(self):
        with open(f'{self.path}/{self.hash}.eaf', 'rb') as fin:
            return fin.read()
