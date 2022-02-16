import pstats
from pstats import SortKey
p = pstats.Stats('profile.prof')
p.sort_stats(SortKey.TIME, SortKey.CUMULATIVE).print_stats()
