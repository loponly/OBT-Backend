import os, sys
import dill
from dill.source import dumpsource

class bc:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def test(filepath):
    with open(filepath, 'br+') as f:
        data = f.read()
        print('Loading from file')
        print(f'{len(data)} bytes')
        x = dill.loads(data)
        try:
            b = x(None)
        except Exception as e:
            # AI Model will not load when instantiated because the file is not available
            # But it will decode which is what we are testing here
            if 'Bad Model' in str(e):
                print(f'{bc.OKGREEN} AI Model detected (OK) {bc.ENDC}')

                # Setup paths for loading the correct model
                abs_path = os.path.realpath('../..')
                if not abs_path in sys.path:
                    sys.path.append(abs_path)

                os.chdir('../..')
                test(filepath)
                return
            else:
                print(f'{bc.FAIL} Model might have failed :( {bc.ENDC}')
                raise e


        print('samples', b.required_samples())
        print('Reloading...')
        data = dill.dumps(x)
        print(f'{len(data)} bytes')
        y = dill.loads(data)
        b = y(None)
        print('samples', b.required_samples())
        print('Reloading again...')
        data = dill.dumps(x)
        print(f'{len(data)} bytes')
        y = dill.loads(data)
        b = y(None)
        print('samples', b.required_samples())
        print(f'{bc.OKGREEN} Strategy OK {bc.ENDC}')

        # TODO: test step

if __name__ == '__main__':
    filename = './strategy-out.dill'
    fpath = os.path.realpath(filename)
    test(fpath)