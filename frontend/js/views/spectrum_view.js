export const spectrumViewTemplate = `
<section class="panel">
  <h2>温谱导出</h2>
  <p class="hint">仅已封批批次可导出；导出请求必须带当前封批代，错代零增拒收；同代重复导出按争用拒绝。</p>
  <div class="form-row">
    <div><label>已封批批次（当前封批代）</label>
      <select v-model.number="spectrumForm.batch_id" @change="syncSpectrumGeneration"><option v-for="b in sealedBatches" :key="b.id" :value="b.id">{{ b.batch_code }} G{{ b.generation }}</option></select>
    </div>
    <div><label>请求封批代</label>
      <input type="number" min="1" v-model.number="spectrumForm.generation"/>
    </div>
  </div>
  <div class="actions"><button @click="doSpectrum">导出温谱</button></div>
  <table class="data-table">
    <thead><tr><th>导出号</th><th>批次</th><th>绑定代</th><th>当前封批代</th><th>代际一致</th><th>点数</th><th>校验</th><th>状态</th></tr></thead>
    <tbody>
      <tr v-for="x in spectrumExports" :key="x.id">
        <td>{{ x.export_ref }}</td>
        <td>{{ x.batch_code }}</td>
        <td>G{{ x.generation }}</td>
        <td>G{{ exportCurrentGeneration(x) }}</td>
        <td><span :class="exportGenerationAligned(x) ? 'ok' : 'bad'">{{ exportGenerationAligned(x) ? "一致" : "错代" }}</span></td>
        <td>{{ x.point_count }}</td>
        <td class="mono">{{ x.checksum }}</td>
        <td>{{ x.status }}</td>
      </tr>
    </tbody>
  </table>
</section>
`;
