# Rust

デバッグモードの計装パターン（Rust 対応）。

## ワンライナー

```rust
// region debug:H1
{use std::fs::OpenOptions;use std::io::Write;if let Ok(mut f)=OpenOptions::new().create(true).append(true).open("debug.log"){let _=writeln!(f,"{{\"h\":\"H1\",\"l\":\"label\",\"v\":{:?},\"ts\":{}}}",value,std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis());}}
// endregion
```

## 展開版

```rust
// region debug:H1
{
    use std::fs::OpenOptions;
    use std::io::Write;

    if let Ok(mut f) = OpenOptions::new()
        .create(true)
        .append(true)
        .open("debug.log")
    {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis();
        let _ = writeln!(f, r#"{{"h":"H1","l":"user_state","v":{:?},"ts":{}}}"#, value, ts);
    }
}
// endregion
```
