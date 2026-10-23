import { useId, useState } from "react";
import type { Template, TemplateField } from "./api";
import { magneticHandlers, Scramble } from "./fx/effects";
import { ImageDrop } from "./ImageDrop";

const magnetic = magneticHandlers();

type Values = Record<string, unknown>;

interface Props {
  template: Template;
  initialValues?: Values;
  submitting: boolean;
  onSubmit: (values: Values, count: number) => void;
}

function defaults(template: Template, initial?: Values): Values {
  const values: Values = {};
  for (const f of template.fields) values[f.key] = initial?.[f.key] ?? f.default ?? "";
  return values;
}

/** 和後端 translate.py 的判斷一致：含中日韓表意文字就需要翻譯 */
export const hasChinese = (text: string) => /[㐀-䶿一-鿿豈-﫿]/.test(text);

/** 從「方形 1024×1024」這類選項文字取出長寬，用來畫比例示意 */
function dimensions(label: string): [number, number] | null {
  const m = label.match(/(\d+)\s*[×x]\s*(\d+)/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

export function Composer({ template, initialValues, submitting, onSubmit }: Props) {
  const [values, setValues] = useState<Values>(() => defaults(template, initialValues));
  const [showAdvanced, setShowAdvanced] = useState(
    () => !!initialValues && template.fields.some((f) => f.advanced && initialValues[f.key] !== f.default),
  );
  const set = (key: string, value: unknown) => setValues((v) => ({ ...v, [key]: value }));

  // 第一個必填的文字欄位當作主角：大字的畫面描述
  const lead = template.fields.find((f) => f.type === "text" && f.required);
  // 圖片欄位放在最前面：先選原圖，再描述要把它變成什麼
  const images = template.fields.filter((f) => f.type === "image");
  const basic = template.fields.filter((f) => !f.advanced && f !== lead && f.type !== "image");
  const advanced = template.fields.filter((f) => f.advanced && f !== lead);
  const leadText = lead ? String(values[lead.key] ?? "") : "";
  const willTranslate = !!lead?.translate && hasChinese(leadText);

  const [count, setCount] = useState(1);
  const submit = () => onSubmit(values, count);

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="lead">
        {lead ? (
          <label htmlFor={`f-${lead.key}`} className="lead-label">
            <Scramble text={template.heading} />
          </label>
        ) : (
          <h1 className="lead-label">
            <Scramble text={template.heading} />
          </h1>
        )}
        {images.map((f) => (
          <Field key={f.key} field={f} value={values[f.key]} onChange={(v) => set(f.key, v)} />
        ))}
        {lead && (
          <>
            <div className="prompt-shell">
              <textarea
                id={`f-${lead.key}`}
                className="lead-input"
                rows={3}
                required
                maxLength={lead.max_length ?? undefined}
                placeholder="例如：懸崖上的燈塔，黃昏，薄霧，油畫風格"
                aria-describedby="lead-help"
                value={leadText}
                onChange={(e) => set(lead.key, e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    e.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              <div className="prompt-foot" aria-hidden>
                <span className={willTranslate ? "lang is-translating" : "lang"}>
                  {willTranslate ? "中 → EN" : "EN"}
                </span>
                <span>
                  {leadText.length}
                  {lead.max_length ? ` / ${lead.max_length}` : ""}
                </span>
              </div>
            </div>
            <p id="lead-help" className="lead-help">
              {willTranslate
                ? "偵測到中文，送出時會先自動翻成英文，模型只看得懂英文。"
                : "中文或英文都可以。寫出主體、場景、光線和畫風，效果最好。"}
            </p>
          </>
        )}
      </div>

      <div className="options">
        {basic.map((f) => (
          <Field key={f.key} field={f} value={values[f.key]} onChange={(v) => set(f.key, v)} />
        ))}
      </div>

      {advanced.length > 0 && (
        <div className="advanced">
          <button
            type="button"
            className="advanced-toggle"
            aria-expanded={showAdvanced}
            onClick={() => setShowAdvanced((s) => !s)}
          >
            <span className="chevron" aria-hidden />
            進階設定
          </button>
          <div className="advanced-body" hidden={!showAdvanced}>
            {advanced.map((f) => (
              <Field key={f.key} field={f} value={values[f.key]} onChange={(v) => set(f.key, v)} />
            ))}
          </div>
        </div>
      )}

      <div className="submit-row">
        <button type="submit" className="cta" disabled={submitting} {...magnetic}>
          <span>{submitting ? "送出中" : count > 1 ? `${template.action} ${count} 張` : template.action}</span>
        </button>
        <fieldset className="count">
          <legend className="visually-hidden">一次生成幾張</legend>
          {[1, 2, 3, 4].map((n) => (
            <label key={n} className="count-option">
              <input type="radio" name="count" value={n} checked={count === n} onChange={() => setCount(n)} />
              <span>×{n}</span>
            </label>
          ))}
        </fieldset>
        <span className="kbd-hint" aria-hidden>
          <kbd>Ctrl</kbd> + <kbd>Enter</kbd>
        </span>
      </div>
    </form>
  );
}

function Field({ field: f, value, onChange }: { field: TemplateField; value: unknown; onChange: (v: unknown) => void }) {
  const id = useId();

  if (f.type === "image") {
    return (
      <div className="field">
        <label className="field-label" htmlFor={id}>
          {f.label}
        </label>
        <ImageDrop id={id} value={String(value ?? "")} onChange={onChange} />
      </div>
    );
  }

  if (f.type === "choice") {
    const shaped = f.options.every((o) => dimensions(o.label));
    return (
      <fieldset className="field">
        <legend className="field-label">{f.label}</legend>
        <div className={shaped ? "segmented shaped" : "segmented"}>
          {f.options.map((o) => {
            const dims = dimensions(o.label);
            const name = dims ? o.label.replace(/\s*\d+\s*[×x]\s*\d+/, "") : o.label;
            return (
              <label key={o.value} className="segment">
                <input
                  type="radio"
                  name={`${id}-${f.key}`}
                  value={o.value}
                  checked={value === o.value}
                  onChange={() => onChange(o.value)}
                />
                {shaped && dims && (
                  <span
                    className="aspect"
                    aria-hidden
                    style={{ aspectRatio: `${dims[0]} / ${dims[1]}` }}
                  />
                )}
                <span className="segment-text">
                  {name}
                  {dims && <small>{dims[0]}×{dims[1]}</small>}
                </span>
              </label>
            );
          })}
        </div>
        {f.help && <p className="field-help">{f.help}</p>}
      </fieldset>
    );
  }

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {f.label}
      </label>
      {f.type === "text" ? (
        <textarea
          id={id}
          rows={2}
          maxLength={f.max_length ?? undefined}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <div className="number-row">
          <input
            id={id}
            type="number"
            inputMode="numeric"
            min={f.type === "seed" ? -1 : f.min ?? undefined}
            max={f.max ?? undefined}
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
          />
          {f.type === "seed" && (
            <button type="button" className="ghost" onClick={() => onChange(-1)} disabled={value === -1}>
              每次隨機
            </button>
          )}
        </div>
      )}
      {f.help && <p className="field-help">{f.help}</p>}
    </div>
  );
}
