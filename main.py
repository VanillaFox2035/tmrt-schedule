import time
import csv
from enum import Enum
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from ortools.sat.python import cp_model

# ------ 定義 ------
class ShiftType(Enum):
  REST    = 0 # 休假 (含R、P、特休)
  DAY     = 1 # 日班 (08:00~17:00)
  MORNING = 2 # 早班 (07:00~15:00)
  EVENING = 3 # 午班 (14:40~22:40)
  NIGHT   = 4 # 夜班 (22:20~07:20)

# 簡化班型
R = ShiftType.REST
D = ShiftType.DAY
M = ShiftType.MORNING
E = ShiftType.EVENING
N = ShiftType.NIGHT

@dataclass
class Worker():
  id: str                             # 員編
  name: str                           # 姓名
  group: str                          # 組別(A或B)
  last_shifts: list[ShiftType]        # 上個月最後6天的班
  expected_rest_count: int            # 欠假天數(包含R+P+特休) (如有長假需求，當月預計總共休幾天就輸入幾天)
  rest_closing_count: int             # 8周16R結算日前欠R天數 (若無則為None)
  shift_preferences: list[int]        # 偏好班型權重 [早, 午, 夜, 日] (較高表示較喜歡)
  day_shift_requests: list[date]      # 日班需求
  hard_rest_requests: list[date]      # 特休/長假
  main_rest_requests: list[date]      # 主要需求
  secondary_rest_requests: list[date] # 次要需求

@dataclass
class InputType():
  start_date: date
  end_date: date
  rest_closing_date: date
  workers: list[Worker]
  shift_table: any

# 考慮上個月最後6天班
LAST_SHIFTS_COUNT = 6

CONSECUTIVES = [] # 班型相同的狀況
TRANSITIONS  = [] # 班型切換的狀況
# 生成以上班型
for s1 in ShiftType:
  for s2 in ShiftType:
    # 班型轉換不考慮休假
    if (s1 == R or s2 == R):
      continue
    # 檢查連續時，日班不視作早班，盡量讓日班連在一起
    if (s1 == s2):
      CONSECUTIVES.append((s1, s2))
    # 檢查切換時，日班視作早班，讓 日->早 或 早->日 不扣分
    st1 = s1 if s1 != D else M
    st2 = s2 if s2 != D else M
    if (st1 != st2):
      TRANSITIONS.append((s1, s2))

WORK_REST = [] # 上班切休假的狀況
REST_WORK = [] # 休假切上班的狀況
for s1 in ShiftType:
  for s2 in ShiftType:
    if (s1 != R and s2 == R):
      WORK_REST.append((s1, s2))
    if (s1 == R and s2 != R):
      REST_WORK.append((s1, s2))

# ------ 輸入資料 -------
def get_inputs(model: cp_model.CpModel):
   # **** 手動鍵入資料中...之後要改 ****
  start_date = date(2026, 9, 6) # 班表開始日期
  end_date = date(2026, 10, 3)  # 班表結束日期(含)
  rest_closing_date = date(2026, 9, 12) # 8周16R結算日(含) (若此月無則為None)
  workers: list[Worker] = []    # 人力列表
  # ----------------------員編-----姓名--組別--------前6天班---當月R數-結算欠R數量--偏好班型---日班--特休--主要--次要--
  workers.append(Worker("110001", "AAA", "A", [R, M, M, M, M, R], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  workers.append(Worker("110002", "BBB", "A", [E, E, R, E, E, E], 9, 2, [0 ,0 ,0 ,0], [date(2026, 9, 8)], [], [date(2026, 9, 10),date(2026, 9, 11)], []))
  workers.append(Worker("110003", "CCC", "A", [M, E, E, R, R, R], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  workers.append(Worker("110004", "DDD", "A", [M, R, N, N, N, N], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  workers.append(Worker("110005", "EEE", "A", [N, N, N, N, R, R], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  workers.append(Worker("110006", "FFF", "A", [N, N, R, R, M, M], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  workers.append(Worker("110007", "GGG", "B", [R, M, M, M, E, E], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  workers.append(Worker("110008", "HHH", "A", [E, R, R, R, R, M], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  workers.append(Worker("110009", "III", "A", [R, R, E, E, N, N], 9, 2, [0 ,0 ,0 ,0], [], [], [date(2026, 9, 15),date(2026, 9, 16)], [date(2026, 9, 29),date(2026, 9, 30)]))
  workers.append(Worker("110010", "JJJ", "A", [R, N, N, N, R, R], 9, 2, [0 ,0 ,0 ,0], [], [], [], []))
  # ********
  shift_table = create_shift_table(model, start_date, end_date, workers)
  input = InputType(start_date, end_date, rest_closing_date, workers, shift_table)
  return input

def create_shift_table(model: cp_model.CpModel, start_date: date, end_date: date, workers: list[Worker]):
  num_workers = len(workers)
  num_days = LAST_SHIFTS_COUNT + (end_date - start_date).days + 1
  # shift_table[worker, day, shift]
  # 若worker在第day天上shift班型，將此值設定為1，否則為0
  # 前6天為上個月的班，第7天才是正式排班
  shift_table = {}
  for w in range(num_workers):
    for d in range(num_days):
      for s in ShiftType:
        date_string = get_date_string(start_date, d)
        var_name = f'shift_{workers[w].id}_{date_string}_{s.name}'
        shift_table[w, d, s.value] = model.new_bool_var(var_name)
  return shift_table

def get_date_string(start_date: date, passed_days: int):
  current_date:date = start_date + timedelta(days=passed_days)
  return current_date.strftime('%m/%d')

# ------ 硬性條件 ------
def set_hard_contraints(model: cp_model.CpModel, data: InputType):
  num_workers = len(data.workers)
  num_days = (data.end_date - data.start_date).days + 1
  shift_table = data.shift_table

  # 先設好前6天的班
  for w in range(num_workers):
    for d in range(0, LAST_SHIFTS_COUNT):
      for s in ShiftType:
        if (data.workers[w].last_shifts[d] == s):
          model.add(shift_table[w, d, s.value] == 1)
        else:
          model.add(shift_table[w, d, s.value] == 0)

  # 每個人每天只能上一種班
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days):
      model.add(sum(shift_table[w, d, s.value] for s in ShiftType) == 1)

  # 早午夜每天各要有2人值班
  for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days):
    model.add(sum(shift_table[w, d, M.value] for w in range(num_workers)) == 2)
    model.add(sum(shift_table[w, d, E.value] for w in range(num_workers)) == 2)
    model.add(sum(shift_table[w, d, N.value] for w in range(num_workers)) == 2)

  # 每個人至少要各上早午夜兩天 (除非休假天數超過當月一半)
  for w in range(num_workers):
    thresh = (data.end_date - data.start_date).days / 2
    if (data.workers[w].expected_rest_count <= thresh):
      model.add(sum(shift_table[w, d, M.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days)) >= 2)
      model.add(sum(shift_table[w, d, E.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days)) >= 2)
      model.add(sum(shift_table[w, d, N.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days)) >= 2)

  # 不可連七
  # (每個連續7天內至少要有1個R)
  MAX_CONTINUOUS_SHIFTS = 6
  for w in range(num_workers):
    for c in range(0, LAST_SHIFTS_COUNT + num_days - MAX_CONTINUOUS_SHIFTS):
      # 7天內就要至少有1R
      start_day = c
      end_day = c + MAX_CONTINUOUS_SHIFTS + 1
      model.add(sum(shift_table[w, d, R.value] for d in range(start_day, end_day)) >= 1)

  # 每種班型要間隔11小時以上
  # (不可: 午->早、夜->早/日、夜->午)
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT - 1, LAST_SHIFTS_COUNT + num_days - 1):
      model.add(shift_table[w, d, E.value] + shift_table[w, d + 1, M.value] <= 1)
      # model.add(shift_table[w, d, E.value] + shift_table[w, d + 1, D.value] <= 1)
      model.add(shift_table[w, d, N.value] + shift_table[w, d + 1, M.value] <= 1)
      model.add(shift_table[w, d, N.value] + shift_table[w, d + 1, D.value] <= 1)
      model.add(shift_table[w, d, N.value] + shift_table[w, d + 1, E.value] <= 1)

  # 8周16R結算
  if (data.rest_closing_date != None):
    rest_closing_date = (data.rest_closing_date - data.start_date).days + 1
    for w in range(num_workers):
      rest_closing_count = data.workers[w].rest_closing_count
      model.add(sum(shift_table[w, d, R.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + rest_closing_date)) == rest_closing_count)

  # 日班需求
  for w in range(num_workers):
    day_shift_requests = data.workers[w].day_shift_requests
    for day_shift_date in day_shift_requests:
      d = LAST_SHIFTS_COUNT + (day_shift_date - data.start_date).days
      model.add(shift_table[w, d, D.value] == 1)

  # 特休/長假需求
  for w in range(num_workers):
    hard_rest_requests = data.workers[w].hard_rest_requests
    for rest_date in hard_rest_requests:
      d = LAST_SHIFTS_COUNT + (rest_date - data.start_date).days
      model.add(shift_table[w, d, R.value] == 1)

  # 不可B+B同時值班
  for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days):
    for s in ShiftType:
      # 每天的每個班型最多只能有一個B組
      model.add(sum((shift_table[w, d, s.value] * (0 if data.workers[w].group == 'A' else 1)) for w in range(num_workers)) <= 1)

# ------ 軟性條件 ------
def set_soft_contraints(model: cp_model.CpModel, data: InputType):
  num_workers = len(data.workers)
  num_days = (data.end_date - data.start_date).days + 1
  shift_table = data.shift_table
  loss = []

  # 符合主要需求
  MAIN_REST_REQ_BONUS = 600
  MAIN_REST_REQ_NIGHT_PENALTY = 500
  for w in range(num_workers):
    for r in data.workers[w].main_rest_requests:
      d = LAST_SHIFTS_COUNT + (r - data.start_date).days
      date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
      is_main_rest_req_set = model.new_bool_var(f'is_main_rest_req_set_{data.workers[w].id}_{date_string}')
      model.add(shift_table[w, d, R.value] == 1).only_enforce_if(is_main_rest_req_set)
      loss.append(is_main_rest_req_set * -MAIN_REST_REQ_BONUS)
      # 如果需求前面排夜班會扣分
      is_night_before_main_req = model.new_bool_var(f'is_night_before_main_req_{data.workers[w].id}_{date_string}')
      model.add(shift_table[w, d - 1, N.value] == 1).only_enforce_if(is_night_before_main_req)
      loss.append(is_night_before_main_req * MAIN_REST_REQ_NIGHT_PENALTY)

  # 符合次要需求
  SECONDARY_REST_REQ_BONUS = 300
  SECONDARY_REST_REQ_NIGHT_PENALTY = 250
  for w in range(num_workers):
    for r in data.workers[w].secondary_rest_requests:
      d = LAST_SHIFTS_COUNT + (r - data.start_date).days
      date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
      is_secondary_rest_req_set = model.new_bool_var(f'is_secondary_rest_req_set_{data.workers[w].id}_{date_string}')
      model.add(shift_table[w, d, ShiftType.REST.value] == 1).only_enforce_if(is_secondary_rest_req_set)
      loss.append(is_secondary_rest_req_set * -SECONDARY_REST_REQ_BONUS)
      # 如果需求前面排夜班會扣分
      is_night_before_secondary_req = model.new_bool_var(f'is_night_before_secondary_req_{data.workers[w].id}_{date_string}')
      model.add(shift_table[w, d - 1, ShiftType.NIGHT.value] == 1).only_enforce_if(is_night_before_secondary_req)
      loss.append(is_night_before_secondary_req * SECONDARY_REST_REQ_NIGHT_PENALTY)

  # 避免 夜R早 和 夜R日
  NIGHT_REST_MORNING_PENALTY = 12000
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT - 2, LAST_SHIFTS_COUNT + num_days - 2):
      date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
      is_night_rest_morning = model.new_bool_var(f'is_night_rest_morning_{data.workers[w].id}_{date_string}')
      model.add_bool_and([shift_table[w, d    , N.value],
                          shift_table[w, d + 1, R.value],
                          shift_table[w, d + 2, M.value],
                          ]).only_enforce_if(is_night_rest_morning)
      loss.append(is_night_rest_morning * NIGHT_REST_MORNING_PENALTY)
      is_night_rest_day = model.new_bool_var(f'is_night_rest_day_{data.workers[w].id}_{date_string}')
      model.add_bool_and([shift_table[w, d    , N.value],
                          shift_table[w, d + 1, R.value],
                          shift_table[w, d + 2, D.value],
                          ]).only_enforce_if(is_night_rest_day)
      loss.append(is_night_rest_day * NIGHT_REST_MORNING_PENALTY)

  # 避免夜R午
  NIGHT_REST_EVENING_PENALTY = 3000
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT - 2, LAST_SHIFTS_COUNT + num_days - 2):
      date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
      is_night_rest_evening = model.new_bool_var(f'is_night_rest_evening_{data.workers[w].id}_{date_string}')
      model.add_bool_and([shift_table[w, d    , N.value],
                          shift_table[w, d + 1, R.value],
                          shift_table[w, d + 2, E.value],
                          ]).only_enforce_if(is_night_rest_evening)
      loss.append(is_night_rest_evening * NIGHT_REST_EVENING_PENALTY)

  # 休假之間班型相同加分
  SHIFT_SAME_BONUS = 5
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT - 1, LAST_SHIFTS_COUNT + num_days - 1):
      for s1, s2 in CONSECUTIVES:
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        is_shift_same = model.new_bool_var(f'is_shift_same_{data.workers[w].id}_{date_string}_{s1.name}_{s2.name}')
        model.add_bool_and([shift_table[w, d, s1.value], shift_table[w, d + 1, s2.value]]).only_enforce_if(is_shift_same)
        loss.append(is_shift_same * -SHIFT_SAME_BONUS)

  # 休假之間班型轉換扣分
  SHIFT_CHANGE_PENALTY = 150
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT - 1, LAST_SHIFTS_COUNT + num_days - 1):
      for s1, s2 in TRANSITIONS:
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        is_shift_change = model.new_bool_var(f'is_shift_change_{data.workers[w].id}_{date_string}_{s1.name}_{s2.name}')
        model.add_bool_and([shift_table[w, d, s1.value], shift_table[w, d + 1, s2.value]]).only_enforce_if(is_shift_change)
        loss.append(is_shift_change * SHIFT_CHANGE_PENALTY)

  # 每次上班切休假或休假切上班就扣分 (避免頻繁單天上班)
  SHIFT_REST_PENALTY = 800
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT - 1, LAST_SHIFTS_COUNT + num_days - 1):
      date_string = get_date_string(data.start_date, d)
      is_shift_rest = model.new_bool_var(f'is_shift_rest_{data.workers[w].id}_{date_string}')
      model.add_bool_and([~shift_table[w, d, R.value],  shift_table[w, d + 1, R.value]]).only_enforce_if(is_shift_rest)
      loss.append(is_shift_rest * SHIFT_REST_PENALTY)
      is_rest_shift = model.new_bool_var(f'is_rest_shift_{data.workers[w].id}_{date_string}')
      model.add_bool_and([ shift_table[w, d, R.value], ~shift_table[w, d + 1, R.value]]).only_enforce_if(is_rest_shift)
      loss.append(is_rest_shift * SHIFT_REST_PENALTY)

  # 夜班切換任何其他班型(含R班)都多扣分，減少夜班段數
  NIGHT_CHANGE_PENALTY = 200
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT - 1, LAST_SHIFTS_COUNT + num_days - 1):
      date_string = get_date_string(data.start_date, d)
      is_night_other = model.new_bool_var(f'is_night_other_{data.workers[w].id}_{date_string}')
      model.add_bool_and([ shift_table[w, d, N.value], ~shift_table[w, d + 1, N.value]]).only_enforce_if(is_night_other)
      loss.append(is_night_other * NIGHT_CHANGE_PENALTY)
      is_other_night = model.new_bool_var(f'is_other_night_{data.workers[w].id}_{date_string}')
      model.add_bool_and([~shift_table[w, d, N.value],  shift_table[w, d + 1, N.value]]).only_enforce_if(is_other_night)
      loss.append(is_other_night * NIGHT_CHANGE_PENALTY)

    # Abs(夜班數量*2 - 早班數量 - 午班數量)越低越好，讓每人夜班數量盡量平均
    NIGHT_DIFF_PENALTY = 100
    for w in range(num_workers):
      morning_count = model.new_int_var(0, num_days, f'morning_count_{data.workers[w].id}')
      evening_count = model.new_int_var(0, num_days, f'evening_count_{data.workers[w].id}')
      night_count   = model.new_int_var(0, num_days, f'night_count_{data.workers[w].id}')
      shift_diff    = model.new_int_var(0, num_days, f'shift_diff_{data.workers[w].id}')
      model.add(morning_count == sum(shift_table[w, d, M.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days)))
      model.add(evening_count == sum(shift_table[w, d, E.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days)))
      model.add(night_count   == sum(shift_table[w, d, N.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days)))
      model.add_abs_equality(shift_diff, night_count * 2 - morning_count - evening_count)
      loss.append(shift_diff * NIGHT_DIFF_PENALTY)

  # 連續上幾天班時的懲罰值，讓連續上班天數盡量接近3~5天
  CONTINUOUS_SHIFT_PENALTIES = {
  1: 200,
  2: 50,
  3: 0,
  4: 0,
  5: 0,
  6: 20
  }
  for w in range(num_workers):
    # 檢查連續上班1~6天的情況
    for c in range(1, 7):
      for d in range(LAST_SHIFTS_COUNT - c, LAST_SHIFTS_COUNT + num_days - c - 1):
        # 生成要對應的滑動視窗
        check_window = [shift_table[w, d, R.value]]
        for i in range(c):
          check_window.append(~shift_table[w, d + i + 1, R.value])
        check_window.append(shift_table[w, d + c + 1, R.value])
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        is_continuous_shift = model.new_bool_var(f'is_continuous_shift_{data.workers[w].id}_{date_string}_R_{c}_R')
        model.add_bool_and(check_window).only_enforce_if(is_continuous_shift)
        expected_penalty = CONTINUOUS_SHIFT_PENALTIES.get(c, 0)
        loss.append(is_continuous_shift * expected_penalty)

  # 連續休假幾天時的加分值，讓連續休假天數盡量接近2~3天
  CONTINUOUS_REST_BONUS = {
      1: 0,
      2: 10,
      3: 10,
      4: 0
  }
  for w in range(num_workers):
    # 檢查連續休假1~4天的情況
    for c in range(1, len(CONTINUOUS_REST_BONUS) + 1):
      for d in range(LAST_SHIFTS_COUNT - c, LAST_SHIFTS_COUNT + num_days - c - 1):
        # 生成要對應的滑動視窗
        check_window = [~shift_table[w, d, R.value]]
        for i in range(c):
          check_window.append(shift_table[w, d + i + 1, R.value])
        check_window.append(~shift_table[w, d + c + 1, R.value])
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        is_continuous_rest = model.new_bool_var(f'is_continuous_rest_{data.workers[w].id}_{date_string}_W_{c}_W')
        model.add_bool_and(check_window).only_enforce_if(is_continuous_rest)
        expected_bonus = CONTINUOUS_REST_BONUS.get(c, 0)
        loss.append(is_continuous_rest * -expected_bonus)

  # 休假數量盡量接近需求數
  REST_DIFF_PENALTY = 500
  for w in range(num_workers):
    target_rest_count = data.workers[w].expected_rest_count
    rest_count = model.new_int_var(0, num_days, f'rest_count_{data.workers[w].id}')
    model.add(rest_count == sum(shift_table[w, d, R.value] for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days)))
    rest_diff = model.new_int_var(0, num_days, f'rest_diff_{data.workers[w].id}')
    model.add_abs_equality(rest_diff, rest_count - target_rest_count)
    loss.append(rest_diff * REST_DIFF_PENALTY)

  # 偏好班型加扣分
  for w in range(num_workers):
    for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days):
      for s in ShiftType:
        preference_mul = 0
        preference_array = data.workers[w].shift_preferences
        match s:
          case ShiftType.MORNING:
            preference_mul = preference_array[0]
          case ShiftType.EVENING:
            preference_mul = preference_array[1]
          case ShiftType.NIGHT  :
            preference_mul = preference_array[2]
          case ShiftType.DAY    :
            preference_mul = preference_array[3]
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        is_preference_set = model.new_bool_var(f'is_preference_get_{data.workers[w].id}_{date_string}_{s.name}')
        model.add(shift_table[w, d, s.value] == 1).only_enforce_if(is_preference_set)
        loss.append(is_preference_set * -preference_mul)

  # 讓懲罰值最小化
  model.minimize(sum(loss))

# ------ 解題 ------
def solve_model(model: cp_model.CpModel, solver: cp_model.CpSolver, solve_time_max = 600.0):
  class ObjectivePrinter(cp_model.CpSolverSolutionCallback):
    def __init__(self):
        cp_model.CpSolverSolutionCallback.__init__(self)
    def on_solution_callback(self):
        now = datetime.now()
        print(f"{now.strftime("%Y-%m-%d %H:%M:%S")} 找到可行解! Loss: {self.ObjectiveValue()}")
  printer = ObjectivePrinter()
  solver.parameters.max_time_in_seconds = solve_time_max
  status = solver.solve(model, printer)
  return status

# ------- 輸出結果 ------
def get_outputs(status: cp_model.CpSolverStatus, solver: cp_model.CpSolver, data: InputType):
  if (status == cp_model.OPTIMAL or status == cp_model.FEASIBLE):
    print('排班成功! ' + '最佳解' if status == cp_model.OPTIMAL else '可行解')
    file_name = "schedule.csv"
    with open(file_name, 'w', newline='', encoding='utf-8') as csv_file:
      writer = csv.writer(csv_file)
      header = ['姓名', '員編']
      num_days = LAST_SHIFTS_COUNT + (data.end_date - data.start_date).days + 1
      shift_table = data.shift_table
      for d in range(num_days):
        header.append(get_date_string(data.start_date, d - LAST_SHIFTS_COUNT))
      writer.writerow(header)
      for w in range(len(data.workers)):
        row = [data.workers[w].name, data.workers[w].id]
        for d in range(num_days):
          if   (solver.value(shift_table[w, d, M.value]) == 1):
            row.append('早')
          elif (solver.value(shift_table[w, d, E.value]) == 1):
            row.append('午')
          elif (solver.value(shift_table[w, d, N.value]) == 1):
            row.append('夜')
          elif (solver.value(shift_table[w, d, D.value]) == 1):
            row.append('日')
          else:
            row.append('R')
        writer.writerow(row)
  else:
    print('排班失敗!請檢查是否有衝突條件')

# ------- 印出檢查項目 ------
def print_checks(status: cp_model.CpSolverStatus, solver: cp_model.CpSolver, data: InputType):
  if (status == cp_model.OPTIMAL or status == cp_model.FEASIBLE):
    num_workers = len(data.workers)
    num_days = (data.end_date - data.start_date).days + 1
    shift_table = data.shift_table
    print('\n---檢查項目---')

    # 未給主要需求
    error = []
    for w in range(num_workers):
      for r in data.workers[w].main_rest_requests:
        d = LAST_SHIFTS_COUNT + (r - data.start_date).days
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        if (solver.value(shift_table[w, d, R.value]) == 0):
          error.append(f'{data.workers[w].id}_{date_string}')
    print(f'未給主要需求: {error}')

    # 未給次要需求
    error = []
    for w in range(num_workers):
      for r in data.workers[w].secondary_rest_requests:
        d = LAST_SHIFTS_COUNT + (r - data.start_date).days
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        if (solver.value(shift_table[w, d, R.value]) == 0):
          error.append(f'{data.workers[w].id}_{date_string}')
    print(f'未給次要需求: {error}')

    # 主要需求前是夜班
    error = []
    for w in range(num_workers):
      for r in data.workers[w].main_rest_requests:
        d = LAST_SHIFTS_COUNT + (r - data.start_date).days
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        if (solver.value(shift_table[w, d - 1, N.value]) == 1):
          error.append(f'{data.workers[w].id}_{date_string}')
    print(f'主要需求前是夜班: {error}')

    # 次要需求前是夜班
    error = []
    for w in range(num_workers):
      for r in data.workers[w].secondary_rest_requests:
        d = LAST_SHIFTS_COUNT + (r - data.start_date).days
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        if (solver.value(shift_table[w, d - 1, N.value]) == 1):
          error.append(f'{data.workers[w].id}_{date_string}')
    print(f'次要需求前是夜班: {error}')

    # 多/欠R數
    error = []
    for w in range(num_workers):
      rest_count = 0
      for d in range(LAST_SHIFTS_COUNT, LAST_SHIFTS_COUNT + num_days):
        if (solver.value(shift_table[w, d, R.value]) == 1):
          rest_count += 1
      expected_rest_count = data.workers[w].expected_rest_count
      if (rest_count > expected_rest_count):
        error.append(f'{data.workers[w].id}_多{rest_count - expected_rest_count}天')
      if (rest_count < expected_rest_count):
        error.append(f'{data.workers[w].id}_欠{expected_rest_count - rest_count}天')
    print(f'多/欠R數: {error}')

    # 夜R早/夜R日/夜R午
    error_M = []
    error_D = []
    error_E = []
    for w in range(num_workers):
      for d in range(LAST_SHIFTS_COUNT - 2, LAST_SHIFTS_COUNT + num_days - 2):
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        if (solver.value(shift_table[w, d, N.value]) == 1):
          if (solver.value(shift_table[w, d + 1, R.value]) == 1):
            if (solver.value(shift_table[w, d + 2, M.value]) == 1):
              error_M.append(f'{data.workers[w].id}_{date_string}')
            if (solver.value(shift_table[w, d + 2, D.value]) == 1):
              error_D.append(f'{data.workers[w].id}_{date_string}')
            if (solver.value(shift_table[w, d + 2, E.value]) == 1):
              error_E.append(f'{data.workers[w].id}_{date_string}')
    print(f'夜R早: {error_M}')
    print(f'夜R日: {error_D}')
    print(f'夜R午: {error_E}')

    # 單天上班
    error = []
    for w in range(num_workers):
      for d in range(LAST_SHIFTS_COUNT - 2, LAST_SHIFTS_COUNT + num_days - 2):
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT + 1)
        if (solver.value(shift_table[w, d, R.value]) == 1):
          if (solver.value(shift_table[w, d + 1, R.value]) == 0):
            if (solver.value(shift_table[w, d + 2, R.value]) == 1):
              error.append(f'{data.workers[w].id}_{date_string}')
    print(f'單天上班: {error}')

    # 連續上班時班型切換
    error = []
    for w in range(num_workers):
      for d in range(LAST_SHIFTS_COUNT - 1, LAST_SHIFTS_COUNT + num_days - 1):
        date_string = get_date_string(data.start_date, d - LAST_SHIFTS_COUNT)
        # 不考慮前後R的狀況
        if (solver.value(shift_table[w, d, R.value]) == 1):
          continue
        if (solver.value(shift_table[w, d + 1, R.value]) == 1):
          continue
        for s in ShiftType:
          if (solver.value(shift_table[w, d, s.value]) != solver.value(shift_table[w, d + 1, s.value])):
            error.append(f'{data.workers[w].id}_{date_string}')
    print(f'連續上班時班型切換: {error}')

# ------- 主函數 ------
def main():
  start_time = time.perf_counter()

  # 初始化模型
  model = cp_model.CpModel()
  solver = cp_model.CpSolver()

  # 輸入資料並設定條件
  data = get_inputs(model)
  set_hard_contraints(model, data)
  set_soft_contraints(model, data)

  # 解題並輸出結果
  status = solve_model(model, solver, 3600.0)
  get_outputs(status, solver, data)
  print_checks(status, solver, data)

  #計時
  end_time = time.perf_counter()
  execution_time = end_time - start_time
  print(f'使用時間:{execution_time:.2f}秒')

if __name__ == '__main__':
  main()
