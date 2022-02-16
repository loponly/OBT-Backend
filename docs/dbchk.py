from routes.db import get_dbs


try:
    dbs = get_dbs()
except:
    print("Failed to open all database please correct manaully")
    exit()


i = 0
for d in dbs:
    try:
        for k in dbs[d]:
            try:
                _ = dbs[d][k]
                i += 1
                if i % 1000 == 0:
                    print('.', end='')
                    if i % 10000 == 0:
                        print()
            except Exception as e:
                print("Error:", e) 
                yn = input(f"Failed to read record {d}::{k}, remove from db? [y/N]")
                if yn.lower() == 'y':
                    dbs[d][k] = b'' # Overwrite record in case of index desync
                    del dbs[d][k]
    except Exception as e:
        print(f"Failed to iter over db {d}")
        raise e

