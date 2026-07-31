# 言語別プロファイリングコマンドリファレンス

## 目次
1. [Python](#python)
2. [Node.js / TypeScript](#nodejs--typescript)
3. [Go](#go)
4. [Java / Kotlin](#java--kotlin)
5. [Rust](#rust)
6. [Ruby](#ruby)

---

## Python

### CPU プロファイリング

```bash
# 標準ライブラリ cProfile（関数ごとの累積時間）
python -m cProfile -s cumtime script.py

# スナップショット形式（実行中プロセスにアタッチ可）
pip install py-spy
py-spy top --pid <PID>
py-spy record -o profile.svg --pid <PID>   # フレームグラフ生成

# line_profiler（行ごとの実行時間）
pip install line_profiler
# 対象関数に @profile デコレータを追加してから:
kernprof -l -v script.py
```

### メモリプロファイリング

```bash
pip install memory-profiler
# 対象関数に @profile デコレータを追加してから:
python -m memory_profiler script.py

# メモリリーク追跡
pip install tracemalloc  # 標準ライブラリ
# コード内で: tracemalloc.start() → snapshot = tracemalloc.take_snapshot()
```

### マイクロベンチマーク

```python
import timeit
result = timeit.timeit('your_function()', setup='from module import your_function', number=1000)
print(f"{result / 1000 * 1000:.3f} ms/call")
```

---

## Node.js / TypeScript

### CPU プロファイリング

```bash
# V8 組み込みプロファイラ
node --prof app.js
node --prof-process isolate-*.log > processed.txt

# 0x（フレームグラフ自動生成）
npm install -g 0x
0x app.js

# clinic.js スイート
npm install -g clinic
clinic doctor -- node app.js   # 診断（CPU/メモリ/I/O）
clinic flame -- node app.js    # フレームグラフ
clinic bubbleprof -- node app.js  # async コールグラフ
```

### メモリプロファイリング

```bash
# Chrome DevTools 経由（Node.js の --inspect フラグ）
node --inspect app.js
# → Chrome で chrome://inspect を開き Heap Snapshot を取得

# heapdump
npm install heapdump
# コード内で: require('heapdump').writeSnapshot('./heap-' + Date.now() + '.heapsnapshot')
```

### マイクロベンチマーク

```bash
npm install -g benchmark
# コード例:
# const suite = new Benchmark.Suite;
# suite.add('fn1', () => fn1()).add('fn2', () => fn2()).run();
```

---

## Go

### CPU / メモリ プロファイリング

```bash
# テストベンチマーク + プロファイル取得
go test -bench=. -cpuprofile=cpu.prof -memprofile=mem.prof ./...

# HTTP サーバーに pprof エンドポイントを追加（本番不可）
import _ "net/http/pprof"
go tool pprof http://localhost:6060/debug/pprof/profile?seconds=30

# プロファイル可視化
go tool pprof -http=:8080 cpu.prof
```

### フレームグラフ

```bash
# go-torch（フレームグラフ）
go install github.com/uber/go-torch@latest
go-torch --binaryname=./app --binaryinput=cpu.prof

# または pprof の -svg オプション
go tool pprof -svg cpu.prof > profile.svg
```

### マイクロベンチマーク

```go
// _test.go ファイル内
func BenchmarkMyFunc(b *testing.B) {
    for i := 0; i < b.N; i++ {
        MyFunc()
    }
}
```

```bash
go test -bench=BenchmarkMyFunc -benchmem -count=5
```

---

## Java / Kotlin

### CPU プロファイリング

```bash
# async-profiler（JVM へのアタッチ、低オーバーヘッド）
./profiler.sh -d 30 -f profile.jfr <PID>
./profiler.sh -d 30 -f flamegraph.svg <PID>

# JFR（Java Flight Recorder、JDK 組み込み）
java -XX:+FlightRecorder -XX:StartFlightRecording=filename=recording.jfr,duration=30s MyApp
jfr print --events CPULoad recording.jfr
```

### メモリプロファイリング

```bash
# ヒープダンプ取得
jmap -dump:format=b,file=heap.hprof <PID>

# Eclipse MAT / VisualVM でヒープダンプを解析
# GC ログ有効化
java -Xlog:gc*:file=gc.log:time,uptime:filecount=5,filesize=20m MyApp
```

### マイクロベンチマーク

```bash
# JMH（Java Microbenchmark Harness）
# pom.xml に jmh 依存追加後:
mvn clean package
java -jar target/benchmarks.jar -wi 5 -i 10
```

---

## Rust

### CPU プロファイリング

```bash
# flamegraph（cargo 拡張）
cargo install flamegraph
cargo flamegraph --bin myapp

# perf（Linux）
perf record --call-graph dwarf ./target/release/myapp
perf report

# samply（macOS / Linux、低オーバーヘッド）
cargo install samply
samply record ./target/release/myapp
```

### マイクロベンチマーク

```bash
# Criterion.rs（統計的ベンチマーク）
# Cargo.toml に criterion を追加後:
cargo bench

# 標準ライブラリ（nightly のみ）
#![feature(test)]
extern crate test;
#[bench] fn bench_fn(b: &mut test::Bencher) { b.iter(|| my_fn()); }
```

---

## Ruby

### CPU プロファイリング

```bash
# stackprof（サンプリング式、低オーバーヘッド）
gem install stackprof
ruby -r stackprof -e "
  StackProf.run(mode: :cpu, out: 'tmp/stackprof.dump') { require './app' }
"
stackprof tmp/stackprof.dump --text --limit 20
stackprof tmp/stackprof.dump --flamegraph > tmp/flamegraph.html

# rack-mini-profiler（Rails アプリ）
gem 'rack-mini-profiler', group: :development
# ページ右上に表示されるプロファイラを確認
```

### メモリプロファイリング

```bash
# memory_profiler gem
gem install memory_profiler
ruby -r memory_profiler -e "
  report = MemoryProfiler.report { require './app' }
  report.pretty_print
"

# derailed_benchmarks（Rails メモリリーク検出）
gem 'derailed_benchmarks', group: :development
bundle exec derailed bundle:mem
bundle exec derailed exec perf:mem_over_time
```

### マイクロベンチマーク

```ruby
require 'benchmark'
Benchmark.bm(20) do |x|
  x.report("方法A:") { 1000.times { method_a } }
  x.report("方法B:") { 1000.times { method_b } }
end
```

---

## 共通: DB クエリ解析

### SQL 実行計画確認

```sql
-- PostgreSQL
EXPLAIN ANALYZE SELECT ...;
EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT ...;

-- MySQL / MariaDB
EXPLAIN FORMAT=JSON SELECT ...;

-- SQLite
EXPLAIN QUERY PLAN SELECT ...;
```

### ORM クエリログ有効化

```python
# Django
import logging
logging.getLogger('django.db.backends').setLevel(logging.DEBUG)

# SQLAlchemy
engine = create_engine(url, echo=True)
```

```ruby
# Rails（development.rb）
config.log_level = :debug
# または ActiveRecord::Base.logger = Logger.new(STDOUT)
```

```typescript
// TypeORM
const dataSource = new DataSource({ logging: true })

// Prisma
const prisma = new PrismaClient({ log: ['query', 'info'] })
```
