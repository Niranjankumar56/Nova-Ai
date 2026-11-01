from multiprocessing import freeze_support
from actions.desktop import _execute_generated_code

if __name__ == "__main__":
    freeze_support()

    result = _execute_generated_code("print('SAFE TEST')")
    print(result)
